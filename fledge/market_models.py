"""Energy market models module."""

from multimethod import multimethod
import numpy as np
import pandas as pd

import fledge.config
import fledge.data_interface
import fledge.utils

logger = fledge.config.get_logger(__name__)

# TODO: Add module tests.


class MarketModel(object):
    """Market model object encapsulating the market clearing behavior."""

    timesteps: pd.Index
    price_timeseries: pd.Series

    @multimethod
    def __init__(
            self,
            scenario_name: str
    ):

        # Obtain data.
        scenario_data = fledge.data_interface.ScenarioData(scenario_name)
        price_data = fledge.data_interface.PriceData(scenario_name)

        self.__init__(
            scenario_data,
            price_data
        )

    @multimethod
    def __init__(
            self,
            scenario_data: fledge.data_interface.ScenarioData,
            price_data: fledge.data_interface.PriceData
    ):

        # Store timesteps index.
        self.timesteps = scenario_data.timesteps

        # Obtain price timeseries.
        try:
            assert pd.notnull(scenario_data.scenario.at['price_type'])
        except AssertionError:
            logger.error(f"No `price_type` defined for scenario: {scenario_data.scenario.at['scenario_name']}")
            raise
        self.price_timeseries = price_data.price_timeseries_dict[scenario_data.scenario.at['price_type']]

    @multimethod
    def clear_market(
            self,
            der_bids: dict
    ):
        """Clear market for given DER bids for all timesteps to obtain cleared prices and DER power dispatch timeseries.
        """

        # Instantiate results variables.
        cleared_prices = pd.Series(index=self.timesteps)
        der_active_power_vector_dispatch = pd.DataFrame(index=self.timesteps, columns=list(der_bids.keys()))

        # Obtain clearing for each timestep.
        for timestep in self.timesteps:
            (
                cleared_prices.at[timestep],
                der_active_power_vector_dispatch.loc[timestep, :]
            ) = self.clear_market(
                {der: bids[timestep] for der, bids in der_bids},
                timestep
            )

        return (
            cleared_prices,
            der_active_power_vector_dispatch
        )

    @multimethod
    def clear_market(
            self,
            der_bids: dict,
            timestep: pd.Timestamp
    ):
        """Clear market for given timestep and DER bids to obtain cleared price and DER power dispatch."""

        # Assert validity of given timestep.
        try:
            assert timestep in self.timesteps
        except AssertionError:
            logger.error(f"Market clearing not possible for invalid timestep: {timestep}")
            raise

        # Obtain cleared price.
        cleared_price = self.price_timeseries.at[timestep, 'price_value']

        # Obtain dispatch power.
        # der_active_power_vector_dispatch = np.zeros(len(der_bids), dtype=np.float)
        # for der_index, der in enumerate(der_bids):
        #
        #     # For loads (negative power).
        #     if der_bids[der].sum() < 0.0:
        #         der_active_power_vector_dispatch[der_index] += (
        #             der_bids[der].loc[der_bids[der].index > cleared_price].sum()
        #         )
        #
        #     # For generators (positive power).
        #     elif der_bids[der].sum() > 0.0:
        #         der_active_power_vector_dispatch[der_index] += (
        #             der_bids[der].loc[der_bids[der].index < cleared_price].sum()
        #         )

        # Obtain dispatch power.
        der_active_power_vector_dispatch = pd.Series(0.0, der_bids.keys())
        for der in der_bids:

            # For loads (negative power).
            if der_bids[der][timestep].sum() < 0.0:
                der_active_power_vector_dispatch[der] += (
                    der_bids[der][timestep].loc[der_bids[der][timestep].index > cleared_price].sum()
                )

            # For generators (positive power).
            elif der_bids[der][timestep].sum() > 0.0:
                der_active_power_vector_dispatch[der] += (
                    der_bids[der][timestep].loc[der_bids[der][timestep].index < cleared_price].sum()
                )

        return (
            cleared_price,
            der_active_power_vector_dispatch
        )