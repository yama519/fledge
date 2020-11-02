"""Energy market models module."""

from multimethod import multimethod
import numpy as np
import pandas as pd
import math
import random

import fledge.config
import fledge.data_interface
import fledge.utils

logger = fledge.config.get_logger(__name__)
np.seterr(all='raise')

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
        """Clear market for given timestep and DER bids to obtain cleared price and DER power dispatch,
        assuming bids are provided as PRICE-QUANTITY PAIRS."""

        # Assert validity of given timestep.
        try:
            assert timestep in self.timesteps
        except AssertionError:
            logger.error(f"Market clearing not possible for invalid timestep: {timestep}")
            raise

        # Obtain cleared price.
        cleared_price = self.price_timeseries.at[timestep, 'price_value']

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

    def clear_market_alt(
            self,
            der_bids: dict,
            timestep: pd.Timestamp
    ):
        """Clear market for given timestep and DER bids to obtain cleared price and DER power dispatch,
        assuming bids are provided as LINEAR CURVES."""

        # Assert validity of given timestep.
        try:
            assert timestep in self.timesteps
        except AssertionError:
            logger.error(f"Market clearing not possible for invalid timestep: {timestep}")
            raise

        # Obtain cleared price.
        cleared_price = self.price_timeseries.at[timestep, 'price_value']

        # Obtain dispatch power.
        der_active_power_vector_dispatch = pd.Series(0.0, der_bids.keys())
        for der in der_bids:

            # For loads (negative power).
            if der_bids[der][timestep].sum() < 0.0:
                if cleared_price < der_bids[der][timestep].index[0]:
                    der_active_power_vector_dispatch[der] += (
                        der_bids[der][timestep].iloc[0]
                    )
                elif cleared_price > der_bids[der][timestep].index[-1]:
                    der_active_power_vector_dispatch[der] += (
                        der_bids[der][timestep].iloc[-1]
                    )
                else:
                    prices = der_bids[der][timestep].index
                    price_intervals = pd.arrays.IntervalArray(
                        [pd.Interval(prices[i], prices[i+1]) for i in range(len(prices)-1)], closed='both'
                    )

                    interval_with_cleared_price = price_intervals[price_intervals.contains(cleared_price)]
                    lower_price_boundary = interval_with_cleared_price.left[0]
                    upper_price_boundary = interval_with_cleared_price.right[0]
                    der_active_power_vector_dispatch[der] += (
                        (cleared_price-lower_price_boundary)/(upper_price_boundary-lower_price_boundary)
                        * (der_bids[der][timestep].loc[upper_price_boundary]-der_bids[der][timestep].loc[lower_price_boundary])
                        + der_bids[der][timestep].loc[lower_price_boundary]
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

    def clear_market_supply_curves(
            self,
            der_bids: dict,
            timestep: pd.Timestamp,
            residual_demand: pd.Series,
            pv_generation: pd.Series,
            scenario='default'
    ):
        """Clear market for given timestep and DER bids to obtain cleared price and DER power dispatch,
        assuming bids are provided as PRICE-QUANTITY PAIRS."""

        # Assert validity of given timestep.
        try:
            assert timestep in self.timesteps
        except AssertionError:
            logger.error(f"Market clearing not possible for invalid timestep: {timestep}")
            raise

        # Obtain aggregate demand.
        price_indexes = der_bids[list(der_bids.keys())[0]][timestep].index
        aggregate_demand = pd.Series(0.0, price_indexes)
        for der in der_bids:
            aggregate_demand += der_bids[der][timestep]
        for price in price_indexes:
            aggregate_demand.loc[price] = aggregate_demand.loc[aggregate_demand.index >= price].sum()

        if scenario == 'default':
            cleared_prices = np.exp(3.258+0.000211 *
                                    (-aggregate_demand/1e6+residual_demand.loc[timestep] - pv_generation.loc[timestep]/1e3)
                                    )/1000
        elif scenario == 'low_price_noon':
            if 10 <= timestep.hour <= 17:
                if timestep.minute != 0:
                    cleared_prices = np.exp(
                        3.258 + 0.000211 * ((-aggregate_demand/1e6+residual_demand.loc[timestep]-pv_generation.loc[timestep]/1e3*2)
                                            )
                    ) / 1000
                else:
                    cleared_prices = np.exp(
                        3.258 + 0.000211 * (-aggregate_demand/1e6 + residual_demand.loc[timestep])
                    ) / 1000
            else:
                cleared_prices = np.exp(
                    3.258 + 0.000211 * ((-aggregate_demand/1e6+residual_demand.loc[timestep]-pv_generation.loc[timestep]/1e3*2)
                                            )
                ) / 1000
        elif scenario == 'random_fluctuations':
            gradient = random.uniform(0.00004, 0.000214)
            cleared_prices = np.exp(
                3.258 + gradient * (-aggregate_demand/1e6+residual_demand.loc[timestep])) / 1000
        elif scenario == 'constant_price':
            cleared_prices = aggregate_demand.copy()
            cleared_prices.loc[:] = 0.03

        # Set cleared price to be the maximum price which is still lower than the bid price
        cleared_price = cleared_prices.loc[cleared_prices.index > cleared_prices].max()
        # print(cleared_price)
        # if cleared_price == np.nan:
        #     cleared_price = cleared_prices.min()

        # Obtain dispatch power.
        der_active_power_vector_dispatch = pd.Series(0.0, der_bids.keys())
        for der in der_bids:

            # For loads (negative power).
            if der_bids[der][timestep].sum() < 0.0:
                der_active_power_vector_dispatch[der] += (
                    der_bids[der][timestep].loc[der_bids[der][timestep].index > cleared_price].sum()
                )
                # if der_active_power_vector_dispatch[der] == 0.0:
                #     der_active_power_vector_dispatch[der] = der_bids[der][timestep].min()

            # For generators (positive power).
            elif der_bids[der][timestep].sum() > 0.0:
                der_active_power_vector_dispatch[der] += (
                    der_bids[der][timestep].loc[der_bids[der][timestep].index < cleared_price].sum()
                )

        return (
            cleared_price,
            der_active_power_vector_dispatch
        )


    def clear_market_supply_curves_alt(
            self,
            der_bids: dict,
            timestep: pd.Timestamp,
            residual_demand: pd.Series,
            pv_generation: pd.Series,
            scenario='default'
    ):
        """Clear market for given timestep and DER bids to obtain cleared price and DER power dispatch,
        assuming bids are provided as LINEAR CURVES."""

        # Assert validity of given timestep.
        try:
            assert timestep in self.timesteps
        except AssertionError:
            logger.error(f"Market clearing not possible for invalid timestep: {timestep}")
            raise

        # Obtain aggregate demand.
        price_indexes = der_bids[list(der_bids.keys())[0]][timestep].index
        aggregate_demand = pd.Series(0.0, price_indexes)
        for der in der_bids:
            aggregate_demand += der_bids[der][timestep]

        # Calculate system-wide demand in MW
        total_demand = -aggregate_demand/1e6 + residual_demand.loc[timestep].values - pv_generation.loc[
                                timestep] / 1e3
        total_demand = total_demand.round()
        # print(total_demand)

        if scenario == 'default':
            cleared_prices = np.exp(3.258 + 0.000211 * total_demand) / 1000

        if len(cleared_prices.unique()) == 1 or cleared_prices.iloc[0] < price_indexes[0]:
            cleared_price = cleared_prices.iloc[0]
        else:
            # print(cleared_prices)
            bid_prices = price_indexes.copy()
            bid_price_intervals = pd.arrays.IntervalArray(
                [pd.Interval(bid_prices[i], bid_prices[i + 1]) for i in range(len(bid_prices) - 1)], closed='both'
            )
            cleared_price_intervals = pd.arrays.IntervalArray(
                [pd.Interval(cleared_prices.iloc[i+1], cleared_prices.iloc[i]) for i in range(len(cleared_prices) - 1)], closed='both'
            )
            # print(bid_price_intervals)
            # print(cleared_price_intervals)
            for bid, cleared in zip(bid_price_intervals, cleared_price_intervals):
                # print(bid, cleared)
                if (cleared.left in bid) or (cleared.right in bid):
                    lower_price_boundary = bid.left
                    upper_price_boundary = bid.right
                    lower_price_dispatch = total_demand.loc[lower_price_boundary]
                    upper_price_dispatch = total_demand.loc[upper_price_boundary]
                    try:
                        gradient = (lower_price_boundary - upper_price_boundary) / (
                                lower_price_dispatch - upper_price_dispatch)
                        intercept = lower_price_boundary - gradient * lower_price_dispatch
                        # print(cleared.left, cleared.right)
                        print(upper_price_dispatch, lower_price_dispatch)
                        gradient_supply = (cleared.left-cleared.right) / (
                                upper_price_dispatch - lower_price_dispatch
                        )
                        intercept_supply = cleared.left-gradient_supply*upper_price_dispatch
                        # print(gradient, intercept)
                        # print(gradient_supply, intercept_supply)
                        cleared_power = (intercept - intercept_supply) / (gradient_supply - gradient)
                        cleared_price = gradient * cleared_power + intercept
                        # print(cleared_power, cleared_price)
                        break
                    except FloatingPointError:
                        cleared_price = cleared.left
                        break

        # Obtain dispatch power.
        der_active_power_vector_dispatch = pd.Series(0.0, der_bids.keys())
        for der in der_bids:

            # For loads (negative power).
            if der_bids[der][timestep].sum() < 0.0:
                if cleared_price < der_bids[der][timestep].index[0]:
                    der_active_power_vector_dispatch[der] += (
                        der_bids[der][timestep].iloc[0]
                    )
                elif cleared_price > der_bids[der][timestep].index[-1]:
                    der_active_power_vector_dispatch[der] += (
                        der_bids[der][timestep].iloc[-1]
                    )
                else:
                    prices = der_bids[der][timestep].index
                    price_intervals = pd.arrays.IntervalArray(
                        [pd.Interval(prices[i], prices[i + 1]) for i in range(len(prices) - 1)], closed='both'
                    )

                    interval_with_cleared_price = price_intervals[price_intervals.contains(cleared_price)]
                    lower_price_boundary = interval_with_cleared_price.left[0]
                    upper_price_boundary = interval_with_cleared_price.right[0]
                    try:
                        der_active_power_vector_dispatch[der] += (
                                (cleared_price - lower_price_boundary) / (
                                    upper_price_boundary - lower_price_boundary)
                                * (der_bids[der][timestep].loc[upper_price_boundary] - der_bids[der][timestep].loc[
                            lower_price_boundary])
                                + der_bids[der][timestep].loc[lower_price_boundary]
                        )
                    except FloatingPointError:
                        der_active_power_vector_dispatch[der] += der_bids[der][timestep].loc[lower_price_boundary]

            # For generators (positive power).
            elif der_bids[der][timestep].sum() > 0.0:
                der_active_power_vector_dispatch[der] += (
                    der_bids[der][timestep].loc[der_bids[der][timestep].index < cleared_price].sum()
                )

        return (
            cleared_price,
            der_active_power_vector_dispatch
        )