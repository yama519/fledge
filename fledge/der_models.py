"""Distributed energy resource (DER) models."""

import inspect
import itertools
from multimethod import multimethod
import numpy as np
import pandas as pd
import pyomo.environ as pyo
import scipy.constants
import sys
import typing

import fledge.config
import fledge.data_interface
import fledge.electric_grid_models
import fledge.thermal_grid_models
import fledge.utils

logger = fledge.config.get_logger(__name__)


class DERModel(object):
    """DER model object."""

    der_type: str = None
    der_name: str
    is_electric_grid_connected: np.bool
    is_thermal_grid_connected: np.bool
    timesteps: pd.Index
    active_power_nominal_timeseries: pd.Series
    reactive_power_nominal_timeseries: pd.Series
    thermal_power_nominal_timeseries: pd.Series

    # TODO: Define method templates.


class FixedDERModel(DERModel):
    """Fixed DER model object."""

    def define_optimization_variables(
            self,
            optimization_problem: pyo.ConcreteModel,
    ):

        # Fixed DERs have no optimization variables.
        pass

    def define_optimization_constraints(
            self,
            optimization_problem: pyo.ConcreteModel,
            electric_grid_model: fledge.electric_grid_models.ElectricGridModelDefault = None,
            power_flow_solution: fledge.electric_grid_models.PowerFlowSolution = None,
            thermal_grid_model: fledge.thermal_grid_models.ThermalGridModel = None,
            thermal_power_flow_solution: fledge.thermal_grid_models.ThermalPowerFlowSolution = None
    ):

        # Define connection constraints.
        if optimization_problem.find_component('der_model_constraints') is None:
            optimization_problem.der_model_constraints = pyo.ConstraintList()

        if (electric_grid_model is not None) and self.is_electric_grid_connected:
            der_index = int(fledge.utils.get_index(electric_grid_model.ders, der_name=self.der_name))
            der = electric_grid_model.ders[der_index]

            for timestep in self.timesteps:
                optimization_problem.der_model_constraints.add(
                    optimization_problem.der_active_power_vector_change[timestep, der]
                    ==
                    self.active_power_nominal_timeseries.at[timestep]
                    - np.real(
                        power_flow_solution.der_power_vector[der_index]
                    )
                )
                optimization_problem.der_model_constraints.add(
                    optimization_problem.der_reactive_power_vector_change[timestep, der]
                    ==
                    self.reactive_power_nominal_timeseries.at[timestep]
                    - np.imag(
                        power_flow_solution.der_power_vector[der_index]
                    )
                )

        if (thermal_grid_model is not None) and self.is_thermal_grid_connected:
            # TODO: Implement fixed load / fixed generator models for thermal grid.
            pass

    def define_optimization_objective(
            self,
            optimization_problem: pyo.ConcreteModel,
            price_data: fledge.data_interface.PriceData,
            electric_grid_model: fledge.electric_grid_models.ElectricGridModelDefault = None,
            thermal_grid_model: fledge.thermal_grid_models.ThermalGridModel = None,
    ):

        # Obtain timestep interval in hours, for conversion of power to energy.
        timestep_interval_hours = (self.timesteps[1] - self.timesteps[0]) / pd.Timedelta('1h')

        # Define objective.
        if optimization_problem.find_component('objective') is None:
            optimization_problem.objective = pyo.Objective(expr=0.0, sense=pyo.minimize)

        # Define objective for electric loads.
        # - If no electric grid model is given, defined here as cost of electric power supply at the DER node.
        # - Otherwise, defined as cost of electric supply at electric grid source node
        #   in `fledge.electric_grid_models.LinearElectricGridModel.define_optimization_objective`.
        if (electric_grid_model is None) and self.is_electric_grid_connected:
            for timestep in self.timesteps:

                # Active power cost / revenue.
                # - Cost for load / demand, revenue for generation / supply.
                optimization_problem.objective.expr += (
                    (
                        price_data.price_timeseries.loc[timestep, ('active_power', slice(None), self.der_name)].iat[0]
                        + price_data.price_sensitivity_coefficient
                        * -1.0 * self.active_power_nominal_timeseries.at[timestep]
                        * timestep_interval_hours  # In Wh.
                    )
                    * -1.0 * self.active_power_nominal_timeseries.at[timestep]
                    * timestep_interval_hours  # In Wh.
                )

                # Reactive power cost / revenue.
                # - Cost for load / demand, revenue for generation / supply.
                optimization_problem.objective.expr += (
                    (
                        price_data.price_timeseries.loc[timestep, ('reactive_power', slice(None), self.der_name)].iat[0]
                        # TODO: Check if sensitivity needed for reactive power cost.
                        + price_data.price_sensitivity_coefficient
                        * -1.0 * self.reactive_power_nominal_timeseries.at[timestep]
                        * timestep_interval_hours  # In Wh.
                    )
                    * -1.0 * self.reactive_power_nominal_timeseries.at[timestep]
                    * timestep_interval_hours  # In Wh.
                )

        # TODO: Define objective for thermal loads.
        # - If no thermal grid model is given, defined here as cost of thermal power supply at the DER node.
        # - Otherwise, defined as cost of thermal supply at thermal grid source node
        #   in `LinearThermalGridModel.define_optimization_objective`.
        # - This enables proper calculation of the DLMPs.
        if (thermal_grid_model is None) and self.is_thermal_grid_connected:
            pass

        # Define objective for electric generators.
        # - Always defined here as the cost of electric power generation at the DER node.
        if self.is_electric_grid_connected:
            if issubclass(type(self), FlexibleGeneratorModel):
                for timestep in self.timesteps:

                    # Active power generation cost.
                    optimization_problem.objective.expr += (
                        self.marginal_cost
                        * self.active_power_nominal_timeseries.at[timestep]
                        * timestep_interval_hours  # In Wh.
                    )

        # TODO: Define objective for thermal generators.
        # - Always defined here as the cost of thermal power generation at the DER node.
        if self.is_thermal_grid_connected:
            pass

    def get_optimization_results(
            self,
            optimization_problem: pyo.ConcreteModel
    ) -> fledge.data_interface.ResultsDict:

        # Fixed DERs have no optimization variables, therefore return empty results.
        return fledge.data_interface.ResultsDict()


class FixedLoadModel(FixedDERModel):
    """Fixed load model object."""

    der_type = 'fixed_load'

    def __init__(
            self,
            der_data: fledge.data_interface.DERData,
            der_name: str
    ):
        """Construct fixed load model object by `der_data` and `der_name`."""

        # Store DER name.
        self.der_name = der_name

        # Get shorthand for DER data.
        der = der_data.ders.loc[self.der_name, :]

        # Obtain grid connection flags.
        # - Fixed loads are currently only implemented for electric grids.
        self.is_electric_grid_connected = pd.notnull(der.at['electric_grid_name'])
        self.is_thermal_grid_connected = False

        # Store timesteps index.
        self.timesteps = der_data.scenario_data.timesteps

        # Construct nominal active and reactive power timeseries.
        self.active_power_nominal_timeseries = (
            der_data.der_definitions[der.at['definition_index']].loc[:, 'value'].copy().abs().rename('active_power')
        )
        self.reactive_power_nominal_timeseries = (
            der_data.der_definitions[der.at['definition_index']].loc[:, 'value'].copy().abs().rename('reactive_power')
        )
        if 'per_unit' in der.at['definition_type']:
            # If per unit definition, multiply nominal active / reactive power.
            self.active_power_nominal_timeseries *= der.at['active_power_nominal']
            self.reactive_power_nominal_timeseries *= der.at['reactive_power_nominal']
        else:
            self.active_power_nominal_timeseries *= np.sign(der.at['active_power_nominal'])
            self.reactive_power_nominal_timeseries *= (
                np.sign(der.at['reactive_power_nominal'])
                * (
                    der.at['reactive_power_nominal'] / der.at['active_power_nominal']
                    if der.at['active_power_nominal'] != 0.0
                    else 1.0
                )
            )

        # Construct nominal thermal power timeseries.
        self.thermal_power_nominal_timeseries = (
            pd.Series(0.0, index=self.timesteps, name='thermal_power')
        )


class FixedEVChargerModel(FixedDERModel):
    """EV charger model object."""

    der_type = 'fixed_ev_charger'

    def __init__(
            self,
            der_data: fledge.data_interface.DERData,
            der_name: str
    ):
        """Construct EV charger model object by `der_data` and `der_name`."""

        # Store DER name.
        self.der_name = der_name

        # Get shorthand for DER data.
        der = der_data.ders.loc[self.der_name, :]

        # Obtain grid connection flags.
        # - EV chargers are currently only implemented for electric grids.
        self.is_electric_grid_connected = pd.notnull(der.at['electric_grid_name'])
        self.is_thermal_grid_connected = False

        # Store timesteps index.
        self.timesteps = der_data.scenario_data.timesteps

        # Construct nominal active and reactive power timeseries.
        self.active_power_nominal_timeseries = (
            der_data.der_definitions[der.at['definition_index']].loc[:, 'value'].copy().abs().rename('active_power')
        )
        self.reactive_power_nominal_timeseries = (
            der_data.der_definitions[der.at['definition_index']].loc[:, 'value'].copy().abs().rename('reactive_power')
        )
        if 'per_unit' in der.at['definition_type']:
            # If per unit definition, multiply nominal active / reactive power.
            self.active_power_nominal_timeseries *= der.at['active_power_nominal']
            self.reactive_power_nominal_timeseries *= der.at['reactive_power_nominal']
        else:
            self.active_power_nominal_timeseries *= np.sign(der.at['active_power_nominal'])
            self.reactive_power_nominal_timeseries *= (
                np.sign(der.at['reactive_power_nominal'])
                * (
                    der.at['reactive_power_nominal'] / der.at['active_power_nominal']
                    if der.at['active_power_nominal'] != 0.0
                    else 1.0
                )
            )

        # Construct nominal thermal power timeseries.
        self.thermal_power_nominal_timeseries = (
            pd.Series(0.0, index=self.timesteps, name='thermal_power')
        )


class FixedGeneratorModel(FixedDERModel):
    """Fixed generator model object, representing a generic generator with fixed nominal output."""

    der_type = 'fixed_generator'
    marginal_cost: np.float

    def __init__(
            self,
            der_data: fledge.data_interface.DERData,
            der_name: str
    ):

        # Store DER name.
        self.der_name = der_name

        # Get shorthand for DER data.
        der = der_data.ders.loc[self.der_name, :]

        # Obtain grid connection flags.
        # - Fixed generators are currently only implemented for electric grids.
        self.is_electric_grid_connected = pd.notnull(der.at['electric_grid_name'])
        self.is_thermal_grid_connected = False

        # Store timesteps index.
        self.timesteps = der_data.scenario_data.timesteps

        # Obtain levelized cost of energy.
        self.marginal_cost = der.at['marginal_cost']

        # Construct nominal active and reactive power timeseries.
        self.active_power_nominal_timeseries = (
            der_data.der_definitions[der.at['definition_index']].loc[:, 'value'].copy().abs().rename('active_power')
        )
        self.reactive_power_nominal_timeseries = (
            der_data.der_definitions[der.at['definition_index']].loc[:, 'value'].copy().abs().rename('reactive_power')
        )
        if 'per_unit' in der.at['definition_type']:
            # If per unit definition, multiply nominal active / reactive power.
            self.active_power_nominal_timeseries *= der.at['active_power_nominal']
            self.reactive_power_nominal_timeseries *= der.at['reactive_power_nominal']
        else:
            self.active_power_nominal_timeseries *= np.sign(der.at['active_power_nominal'])
            self.reactive_power_nominal_timeseries *= (
                np.sign(der.at['reactive_power_nominal'])
                * (
                    der.at['reactive_power_nominal'] / der.at['active_power_nominal']
                    if der.at['active_power_nominal'] != 0.0
                    else 1.0
                )
            )

        # Construct nominal thermal power timeseries.
        self.thermal_power_nominal_timeseries = (
            pd.Series(0.0, index=self.timesteps, name='thermal_power')
        )


class FlexibleDERModel(DERModel):
    """Flexible DER model, e.g., flexible load, object."""

    states: pd.Index
    storage_states: pd.Index = pd.Index([])
    controls: pd.Index
    disturbances: pd.Index
    outputs: pd.Index
    state_vector_initial: pd.Series
    state_matrix: pd.DataFrame
    control_matrix: pd.DataFrame
    disturbance_matrix: pd.DataFrame
    state_output_matrix: pd.DataFrame
    control_output_matrix: pd.DataFrame
    disturbance_output_matrix: pd.DataFrame
    disturbance_timeseries: pd.DataFrame
    output_maximum_timeseries: pd.DataFrame
    output_minimum_timeseries: pd.DataFrame

    def define_optimization_variables(
            self,
            optimization_problem: pyo.ConcreteModel,
    ):

        # Define variables.
        optimization_problem.state_vector = pyo.Var(self.timesteps, [self.der_name], self.states)
        optimization_problem.control_vector = pyo.Var(self.timesteps, [self.der_name], self.controls)
        optimization_problem.output_vector = pyo.Var(self.timesteps, [self.der_name], self.outputs)

    def define_optimization_constraints(
        self,
        optimization_problem: pyo.ConcreteModel,
        electric_grid_model: fledge.electric_grid_models.ElectricGridModelDefault = None,
        power_flow_solution: fledge.electric_grid_models.PowerFlowSolution = None,
        thermal_grid_model: fledge.thermal_grid_models.ThermalGridModel = None,
        thermal_power_flow_solution: fledge.thermal_grid_models.ThermalPowerFlowSolution = None
    ):

        # Define shorthand for indexing 't+1'.
        # - This implementation assumes that timesteps are always equally spaced.
        timestep_interval = self.timesteps[1] - self.timesteps[0]

        # Define constraints.
        if optimization_problem.find_component('der_model_constraints') is None:
            optimization_problem.der_model_constraints = pyo.ConstraintList()

        # Initial state.
        for state in self.states:
            if state in self.storage_states:
                # For states which represent storage state of charge, initial state of charge is final state of charge.
                optimization_problem.der_model_constraints.add(
                    optimization_problem.state_vector[self.timesteps[0], self.der_name, state]
                    ==
                    sum(
                        self.state_matrix.at[state, state_other]
                        * optimization_problem.state_vector[self.timesteps[-1], self.der_name, state_other]
                        for state_other in self.states
                    )
                    + sum(
                        self.control_matrix.at[state, control]
                        * optimization_problem.control_vector[self.timesteps[-1], self.der_name, control]
                        for control in self.controls
                    )
                    + sum(
                        self.disturbance_matrix.at[state, disturbance]
                        * self.disturbance_timeseries.at[self.timesteps[-1], disturbance]
                        for disturbance in self.disturbances
                    )
                )
            else:
                # For other states, set initial state according to the initial state vector.
                optimization_problem.der_model_constraints.add(
                    optimization_problem.state_vector[self.timesteps[0], self.der_name, state]
                    ==
                    self.state_vector_initial.at[state]
                )

        for timestep in self.timesteps[:-1]:

            # State equation.
            for state in self.states:
                optimization_problem.der_model_constraints.add(
                    optimization_problem.state_vector[timestep + timestep_interval, self.der_name, state]
                    ==
                    sum(
                        self.state_matrix.at[state, state_other]
                        * optimization_problem.state_vector[timestep, self.der_name, state_other]
                        for state_other in self.states
                    )
                    + sum(
                        self.control_matrix.at[state, control]
                        * optimization_problem.control_vector[timestep, self.der_name, control]
                        for control in self.controls
                    )
                    + sum(
                        self.disturbance_matrix.at[state, disturbance]
                        * self.disturbance_timeseries.at[timestep, disturbance]
                        for disturbance in self.disturbances
                    )
                )

        for timestep in self.timesteps:

            # Output equation.
            for output in self.outputs:
                optimization_problem.der_model_constraints.add(
                    optimization_problem.output_vector[timestep, self.der_name, output]
                    ==
                    sum(
                        self.state_output_matrix.at[output, state]
                        * optimization_problem.state_vector[timestep, self.der_name, state]
                        for state in self.states
                    )
                    + sum(
                        self.control_output_matrix.at[output, control]
                        * optimization_problem.control_vector[timestep, self.der_name, control]
                        for control in self.controls
                    )
                    + sum(
                        self.disturbance_output_matrix.at[output, disturbance]
                        * self.disturbance_timeseries.at[timestep, disturbance]
                        for disturbance in self.disturbances
                    )
                )

            # Output limits.
            for output in self.outputs:
                optimization_problem.der_model_constraints.add(
                    optimization_problem.output_vector[timestep, self.der_name, output]
                    >=
                    self.output_minimum_timeseries.at[timestep, output]
                )
                optimization_problem.der_model_constraints.add(
                    optimization_problem.output_vector[timestep, self.der_name, output]
                    <=
                    self.output_maximum_timeseries.at[timestep, output]
                )

        # Define connection constraints.
        if (electric_grid_model is not None) and self.is_electric_grid_connected:
            der_index = int(fledge.utils.get_index(electric_grid_model.ders, der_name=self.der_name))
            der = electric_grid_model.ders[der_index]

            if type(self) is FlexibleBuildingModel:
                for timestep in self.timesteps:
                    optimization_problem.der_model_constraints.add(
                        optimization_problem.der_active_power_vector_change[timestep, der]
                        ==
                        -1.0 * optimization_problem.output_vector[timestep, self.der_name, 'grid_electric_power']
                        - np.real(
                            power_flow_solution.der_power_vector[der_index]
                        )
                    )
                    optimization_problem.der_model_constraints.add(
                        optimization_problem.der_reactive_power_vector_change[timestep, der]
                        ==
                        -1.0 * (
                            optimization_problem.output_vector[timestep, self.der_name, 'grid_electric_power']
                            * np.tan(np.arccos(self.power_factor_nominal))
                        )
                        - np.imag(
                            power_flow_solution.der_power_vector[der_index]
                        )
                    )
            else:
                for timestep in self.timesteps:
                    optimization_problem.der_model_constraints.add(
                        optimization_problem.der_active_power_vector_change[timestep, der]
                        ==
                        optimization_problem.output_vector[timestep, self.der_name, 'active_power']
                        - np.real(
                            power_flow_solution.der_power_vector[der_index]
                        )
                    )
                    optimization_problem.der_model_constraints.add(
                        optimization_problem.der_reactive_power_vector_change[timestep, der]
                        ==
                        optimization_problem.output_vector[timestep, self.der_name, 'reactive_power']
                        - np.imag(
                            power_flow_solution.der_power_vector[der_index]
                        )
                    )

        if (thermal_grid_model is not None) and self.is_thermal_grid_connected:
            der_index = int(fledge.utils.get_index(thermal_grid_model.ders, der_name=self.der_name))
            der = thermal_grid_model.ders[der_index]

            if type(self) is FlexibleBuildingModel:
                for timestep in self.timesteps:
                    optimization_problem.der_model_constraints.add(
                        optimization_problem.der_thermal_power_vector[timestep, der]
                        ==
                        -1.0 * optimization_problem.output_vector[timestep, self.der_name, 'grid_thermal_power_cooling']
                    )
            elif type(self) is CoolingPlantModel:
                for timestep in self.timesteps:
                    optimization_problem.der_model_constraints.add(
                        optimization_problem.der_thermal_power_vector[timestep, der]
                        ==
                        optimization_problem.output_vector[timestep, self.der_name, 'thermal_power']
                    )

    def define_optimization_objective(
            self,
            optimization_problem: pyo.ConcreteModel,
            price_data: fledge.data_interface.PriceData,
            electric_grid_model: fledge.electric_grid_models.ElectricGridModelDefault = None,
            thermal_grid_model: fledge.thermal_grid_models.ThermalGridModel = None,
    ):

        # Obtain timestep interval in hours, for conversion of power to energy.
        timestep_interval_hours = (self.timesteps[1] - self.timesteps[0]) / pd.Timedelta('1h')

        # Define objective.
        # TODO: Consider timestep interval.
        if optimization_problem.find_component('objective') is None:
            optimization_problem.objective = pyo.Objective(expr=0.0, sense=pyo.minimize)

        # Define objective for electric loads.
        # - If no electric grid model is given, defined here as cost of electric power supply at the DER node.
        # - Otherwise, defined as cost of electric supply at electric grid source node
        #   in `fledge.electric_grid_models.LinearElectricGridModel.define_optimization_objective`.
        if (electric_grid_model is None) and self.is_electric_grid_connected:
            if type(self) is FlexibleBuildingModel:
                for timestep in self.timesteps:

                    # Active power cost.
                    optimization_problem.objective.expr += (
                        (
                            price_data.price_timeseries.loc[timestep, ('active_power', slice(None), self.der_name)].iat[0]
                            + price_data.price_sensitivity_coefficient
                            * optimization_problem.output_vector[timestep, self.der_name, 'grid_electric_power']
                            * timestep_interval_hours  # In Wh.
                        )
                        * optimization_problem.output_vector[timestep, self.der_name, 'grid_electric_power']
                        * timestep_interval_hours  # In Wh.
                    )

                    # Reactive power cost.
                    optimization_problem.objective.expr += (
                        (
                            price_data.price_timeseries.loc[timestep, ('reactive_power', slice(None), self.der_name)].iat[0]
                            + price_data.price_sensitivity_coefficient
                            * (
                                optimization_problem.output_vector[timestep, self.der_name, 'grid_electric_power']
                                * np.tan(np.arccos(self.power_factor_nominal))
                            )
                            * timestep_interval_hours  # In Wh.
                        )
                        * (
                            optimization_problem.output_vector[timestep, self.der_name, 'grid_electric_power']
                            * np.tan(np.arccos(self.power_factor_nominal))
                        )
                        * timestep_interval_hours  # In Wh.
                    )

            else:
                for timestep in self.timesteps:

                    # Active power cost / revenue.
                    # - Cost for load / demand, revenue for generation / supply.
                    optimization_problem.objective.expr += (
                        (
                            price_data.price_timeseries.loc[timestep, ('active_power', slice(None), self.der_name)].iat[0]
                            + price_data.price_sensitivity_coefficient
                            * -1.0 * optimization_problem.output_vector[timestep, self.der_name, 'active_power']
                            * timestep_interval_hours  # In Wh.
                        )
                        * -1.0 * optimization_problem.output_vector[timestep, self.der_name, 'active_power']
                        * timestep_interval_hours  # In Wh.
                    )

                    # Reactive power cost / revenue.
                    # - Cost for load / demand, revenue for generation / supply.
                    optimization_problem.objective.expr += (
                        (
                            price_data.price_timeseries.loc[timestep, ('reactive_power', slice(None), self.der_name)].iat[0]
                            # TODO: Check if sensitivity needed for reactive power cost.
                            + price_data.price_sensitivity_coefficient
                            * -1.0 * optimization_problem.output_vector[timestep, self.der_name, 'reactive_power']
                            * timestep_interval_hours  # In Wh.
                        )
                        * -1.0 * optimization_problem.output_vector[timestep, self.der_name, 'reactive_power']
                        * timestep_interval_hours  # In Wh.
                    )

        # TODO: Define objective for thermal loads.
        # - If no thermal grid model is given, defined here as cost of thermal power supply at the DER node.
        # - Otherwise, defined as cost of thermal supply at thermal grid source node
        #   in `LinearThermalGridModel.define_optimization_objective`.
        # - This enables proper calculation of the DLMPs.
        if (thermal_grid_model is None) and self.is_thermal_grid_connected:
            pass

        # Define objective for electric generators.
        # - Always defined here as the cost of electric power generation at the DER node.
        if self.is_electric_grid_connected:
            if issubclass(type(self), FlexibleGeneratorModel):
                for timestep in self.timesteps:

                    # Active power generation cost.
                    optimization_problem.objective.expr += (
                        self.marginal_cost
                        * optimization_problem.output_vector[timestep, self.der_name, 'active_power']
                        * timestep_interval_hours  # In Wh.
                    )

        # TODO: Define objective for thermal generators.
        # - Always defined here as the cost of thermal power generation at the DER node.
        if self.is_thermal_grid_connected:
            pass

    def get_optimization_results(
            self,
            optimization_problem: pyo.ConcreteModel
    ) -> fledge.data_interface.ResultsDict:

        # Instantiate results variables.
        state_vector = pd.DataFrame(0.0, index=self.timesteps, columns=self.states)
        control_vector = pd.DataFrame(0.0, index=self.timesteps, columns=self.controls)
        output_vector = pd.DataFrame(0.0, index=self.timesteps, columns=self.outputs)

        # Solve optimization problem
        solver = pyo.SolverFactory('gurobi')
        solver.solve(optimization_problem)

        # Obtain results.
        for timestep in self.timesteps:
            for state in self.states:
                state_vector.at[timestep, state] = (
                    optimization_problem.state_vector[timestep, self.der_name, state].value
                )
            for control in self.controls:
                control_vector.at[timestep, control] = (
                    optimization_problem.control_vector[timestep, self.der_name, control].value
                )
            for output in self.outputs:
                output_vector.at[timestep, output] = (
                    optimization_problem.output_vector[timestep, self.der_name, output].value
                )

        return fledge.data_interface.ResultsDict(
            state_vector=state_vector,
            control_vector=control_vector,
            output_vector=output_vector
        )


class FlexibleLoadModel(FlexibleDERModel):
    """Flexible load model object."""

    der_type = 'flexible_load'

    def __init__(
            self,
            der_data: fledge.data_interface.DERData,
            der_name: str
    ):
        """Construct flexible load model object by `der_data` and `der_name`."""

        # Store DER name.
        self.der_name = der_name

        # Get shorthand for DER data.
        der = der_data.ders.loc[self.der_name, :]

        # Obtain grid connection flags.
        # - Flexible loads are currently only implemented for electric grids.
        self.is_electric_grid_connected = pd.notnull(der.at['electric_grid_name'])
        self.is_thermal_grid_connected = False

        # Store timesteps index.
        self.timesteps = der_data.scenario_data.timesteps

        # Construct active and reactive power timeseries.
        self.active_power_nominal_timeseries = (
            der_data.der_definitions[der.at['definition_index']].loc[:, 'value'].copy().abs().rename('active_power')
        )
        self.reactive_power_nominal_timeseries = (
            der_data.der_definitions[der.at['definition_index']].loc[:, 'value'].copy().abs().rename('reactive_power')
        )
        if 'per_unit' in der.at['definition_type']:
            # If per unit definition, multiply nominal active / reactive power.
            self.active_power_nominal_timeseries *= der.at['active_power_nominal']
            self.reactive_power_nominal_timeseries *= der.at['reactive_power_nominal']
        else:
            self.active_power_nominal_timeseries *= np.sign(der.at['active_power_nominal'])
            self.reactive_power_nominal_timeseries *= (
                np.sign(der.at['reactive_power_nominal'])
                * (
                    der.at['reactive_power_nominal'] / der.at['active_power_nominal']
                    if der.at['active_power_nominal'] != 0.0
                    else 1.0
                )
            )

        # Construct nominal thermal power timeseries.
        self.thermal_power_nominal_timeseries = (
            pd.Series(0.0, index=self.timesteps, name='thermal_power')
        )

        # Instantiate indexes.
        self.states = pd.Index(['state_of_charge'])
        self.storage_states = pd.Index(['state_of_charge'])
        self.controls = pd.Index(['active_power'])
        self.disturbances = pd.Index(['active_power'])
        self.outputs = pd.Index(['state_of_charge', 'active_power', 'reactive_power'])

        # Instantiate initial state.
        # - Note that this is not used for `storage_states`, whose initial state is coupled with their final state.
        self.state_vector_initial = (
            pd.Series(0.0, index=self.states)
        )

        # Instantiate state space matrices.
        # TODO: Add shifting losses / self discharge.
        self.state_matrix = (
            pd.DataFrame(0.0, index=self.states, columns=self.states)
        )
        self.state_matrix.at['state_of_charge', 'state_of_charge'] = 1.0
        self.control_matrix = (
            pd.DataFrame(0.0, index=self.states, columns=self.controls)
        )
        self.control_matrix.at['state_of_charge', 'active_power'] = (
            -1.0
        )
        self.disturbance_matrix = (
            pd.DataFrame(0.0, index=self.states, columns=self.disturbances)
        )
        self.disturbance_matrix.at['state_of_charge', 'active_power'] = (
            1.0
        )
        self.state_output_matrix = (
            pd.DataFrame(0.0, index=self.outputs, columns=self.states)
        )
        self.state_output_matrix.at['state_of_charge', 'state_of_charge'] = 1.0
        self.control_output_matrix = (
            pd.DataFrame(0.0, index=self.outputs, columns=self.controls)
        )
        self.control_output_matrix.at['active_power', 'active_power'] = 1.0
        self.control_output_matrix.at['reactive_power', 'active_power'] = (
            der.at['reactive_power_nominal'] / der.at['active_power_nominal']
            if der.at['active_power_nominal'] != 0.0
            else 0.0
        )
        self.disturbance_output_matrix = (
            pd.DataFrame(0.0, index=self.outputs, columns=self.disturbances)
        )

        # Instantiate disturbance timeseries.
        self.disturbance_timeseries = (
            self.active_power_nominal_timeseries.to_frame()
        )

        # Construct output constraint timeseries
        self.output_maximum_timeseries = (
            pd.concat([
                pd.Series((
                    np.abs(der['active_power_nominal'] if der['active_power_nominal'] != 0.0 else 1.0)
                    * der['energy_storage_capacity_per_unit']
                    * (pd.Timedelta('1h') / der_data.scenario_data.scenario.at['timestep_interval'])
                ), index=self.active_power_nominal_timeseries.index, name='state_of_charge'),
                (
                    der['power_per_unit_minimum']  # Take minimum, because load is negative power.
                    * self.active_power_nominal_timeseries
                ),
                (
                    der['power_per_unit_minimum']  # Take minimum, because load is negative power.
                    * self.reactive_power_nominal_timeseries
                )
            ], axis='columns')
        )
        self.output_minimum_timeseries = (
            pd.concat([
                pd.Series(0.0, index=self.active_power_nominal_timeseries.index, name='state_of_charge'),
                (
                    der['power_per_unit_maximum']  # Take maximum, because load is negative power.
                    * self.active_power_nominal_timeseries
                ),
                (
                    der['power_per_unit_maximum']  # Take maximum, because load is negative power.
                    * self.reactive_power_nominal_timeseries
                )
            ], axis='columns')
        )


class FlexibleGeneratorModel(FlexibleDERModel):
    """Fixed generator model object, representing a generic generator with fixed nominal output."""

    der_type = 'flexible_generator'
    marginal_cost: np.float

    def __init__(
            self,
            der_data: fledge.data_interface.DERData,
            der_name: str
    ):

        # Store DER name.
        self.der_name = der_name

        # Get shorthand for DER data.
        der = der_data.ders.loc[self.der_name, :]

        # Obtain grid connection flags.
        # - Flexible generators are currently only implemented for electric grids.
        self.is_electric_grid_connected = pd.notnull(der.at['electric_grid_name'])
        self.is_thermal_grid_connected = False

        # Store timesteps index.
        self.timesteps = der_data.scenario_data.timesteps

        # Obtain levelized cost of energy.
        self.marginal_cost = der.at['marginal_cost']

        # Construct nominal active and reactive power timeseries.
        self.active_power_nominal_timeseries = (
            der_data.der_definitions[der.at['definition_index']].loc[:, 'value'].copy().abs().rename('active_power')
        )
        self.reactive_power_nominal_timeseries = (
            der_data.der_definitions[der.at['definition_index']].loc[:, 'value'].copy().abs().rename('reactive_power')
        )
        if 'per_unit' in der.at['definition_type']:
            # If per unit definition, multiply nominal active / reactive power.
            self.active_power_nominal_timeseries *= der.at['active_power_nominal']
            self.reactive_power_nominal_timeseries *= der.at['reactive_power_nominal']
        else:
            self.active_power_nominal_timeseries *= np.sign(der.at['active_power_nominal'])
            self.reactive_power_nominal_timeseries *= (
                np.sign(der.at['reactive_power_nominal'])
                * (
                    der.at['reactive_power_nominal'] / der.at['active_power_nominal']
                    if der.at['active_power_nominal'] != 0.0
                    else 1.0
                )
            )

        # Construct nominal thermal power timeseries.
        self.thermal_power_nominal_timeseries = (
            pd.Series(0.0, index=self.timesteps, name='thermal_power')
        )

        # Instantiate indexes.
        self.states = pd.Index([])
        self.controls = pd.Index(['active_power'])
        self.disturbances = pd.Index([])
        self.outputs = pd.Index(['active_power', 'reactive_power'])

        # Instantiate initial state.
        self.state_vector_initial = (
            pd.Series(0.0, index=self.states)
        )

        # Instantiate state space matrices.
        self.state_matrix = (
            pd.DataFrame(0.0, index=self.states, columns=self.states)
        )
        self.control_matrix = (
            pd.DataFrame(0.0, index=self.states, columns=self.controls)
        )
        self.disturbance_matrix = (
            pd.DataFrame(0.0, index=self.states, columns=self.disturbances)
        )
        self.state_output_matrix = (
            pd.DataFrame(0.0, index=self.outputs, columns=self.states)
        )
        self.control_output_matrix = (
            pd.DataFrame(0.0, index=self.outputs, columns=self.controls)
        )
        self.control_output_matrix.at['active_power', 'active_power'] = 1.0
        self.control_output_matrix.at['reactive_power', 'active_power'] = (
            der.at['reactive_power_nominal'] / der.at['active_power_nominal']
            if der.at['active_power_nominal'] != 0.0
            else 0.0
        )
        self.disturbance_output_matrix = (
            pd.DataFrame(0.0, index=self.outputs, columns=self.disturbances)
        )

        # Instantiate disturbance timeseries.
        self.disturbance_timeseries = (
            pd.DataFrame(0.0, index=self.active_power_nominal_timeseries.index, columns=self.disturbances)
        )

        # Construct output constraint timeseries
        self.output_maximum_timeseries = (
            pd.concat([
                self.active_power_nominal_timeseries,
                self.reactive_power_nominal_timeseries
            ], axis='columns')
        )
        self.output_minimum_timeseries = (
            pd.concat([
                0.0 * self.active_power_nominal_timeseries,
                0.0 * self.reactive_power_nominal_timeseries
            ], axis='columns')
        )


class StorageModel(FlexibleDERModel):
    """Energy storage model object."""

    der_type = 'storage'

    def __init__(
            self,
            der_data: fledge.data_interface.DERData,
            der_name: str
    ):

        # Store DER name.
        self.der_name = der_name

        # Get shorthand for DER data.
        der = der_data.ders.loc[self.der_name, :]

        # Obtain grid connection flags.
        # - Currently only implemented for electric grids.
        self.is_electric_grid_connected = pd.notnull(der.at['electric_grid_name'])
        self.is_thermal_grid_connected = False

        # Store timesteps index.
        self.timesteps = der_data.scenario_data.timesteps

        # Construct nominal active and reactive power timeseries.
        self.active_power_nominal_timeseries = (
            pd.Series(0.0, index=self.timesteps, name='active_power')
        )
        self.reactive_power_nominal_timeseries = (
            pd.Series(0.0, index=self.timesteps, name='reactive_power')
        )

        # Construct nominal thermal power timeseries.
        self.thermal_power_nominal_timeseries = (
            pd.Series(0.0, index=self.timesteps, name='thermal_power')
        )

        # Instantiate indexes.
        self.states = pd.Index(['state_of_charge'])
        self.storage_states = pd.Index(['state_of_charge'])
        self.controls = pd.Index(['active_power_charge', 'active_power_discharge'])
        self.disturbances = pd.Index([])
        self.outputs = (
            pd.Index([
                'state_of_charge', 'active_power_charge', 'active_power_discharge',
                'active_power', 'reactive_power'
            ])
        )

        # Instantiate initial state.
        # - Note that this is not used for `storage_states`, whose initial state is coupled with their final state.
        self.state_vector_initial = (
            pd.Series(0.0, index=self.states)
        )

        # Instantiate state space matrices.
        self.state_matrix = (
            pd.DataFrame(0.0, index=self.states, columns=self.states)
        )
        self.state_matrix.at['state_of_charge', 'state_of_charge'] = (
            1.0
            - der['self_discharge_rate']
            * (der_data.scenario_data.scenario.at['timestep_interval'].seconds / 3600.0)
        )
        self.control_matrix = (
            pd.DataFrame(0.0, index=self.states, columns=self.controls)
        )
        self.control_matrix.at['state_of_charge', 'active_power_charge'] = (
            der['charging_efficiency']
            * der_data.scenario_data.scenario.at['timestep_interval']
            / (der['active_power_nominal'] if der['active_power_nominal'] != 0.0 else 1.0)
            / (der['energy_storage_capacity_per_unit'] * pd.Timedelta('1h'))
        )
        self.control_matrix.at['state_of_charge', 'active_power_discharge'] = (
            -1.0
            * der_data.scenario_data.scenario.at['timestep_interval']
            / (der['active_power_nominal'] if der['active_power_nominal'] != 0.0 else 1.0)
            / (der['energy_storage_capacity_per_unit'] * pd.Timedelta('1h'))
        )
        self.disturbance_matrix = (
            pd.DataFrame(0.0, index=self.states, columns=self.disturbances)
        )
        self.state_output_matrix = (
            pd.DataFrame(0.0, index=self.outputs, columns=self.states)
        )
        self.state_output_matrix.at['state_of_charge', 'state_of_charge'] = 1.0
        self.control_output_matrix = (
            pd.DataFrame(0.0, index=self.outputs, columns=self.controls)
        )
        self.control_output_matrix.at['active_power_charge', 'active_power_charge'] = 1.0
        self.control_output_matrix.at['active_power_discharge', 'active_power_discharge'] = 1.0
        self.control_output_matrix.at['active_power', 'active_power_charge'] = -1.0
        self.control_output_matrix.at['active_power', 'active_power_discharge'] = 1.0
        self.control_output_matrix.at['reactive_power', 'active_power'] = (
            der.at['reactive_power_nominal'] / der.at['active_power_nominal']
            if der.at['active_power_nominal'] != 0.0
            else 0.0
        )
        self.disturbance_output_matrix = (
            pd.DataFrame(0.0, index=self.outputs, columns=self.disturbances)
        )

        # Instantiate disturbance timeseries.
        self.disturbance_timeseries = (
            pd.DataFrame(0.0, index=self.active_power_nominal_timeseries.index, columns=self.disturbances)
        )

        # Construct output constraint timeseries
        self.output_maximum_timeseries = (
            pd.concat([
                pd.Series(1.0, index=self.active_power_nominal_timeseries.index, name='state_of_charge'),
                pd.Series(np.inf, index=self.active_power_nominal_timeseries.index, name='active_power_charge'),
                pd.Series(np.inf, index=self.active_power_nominal_timeseries.index, name='active_power_discharge'),
                (
                    der['power_per_unit_maximum']
                    * der['active_power_nominal']
                    * pd.Series(1.0, index=self.active_power_nominal_timeseries.index, name='active_power')
                ),
                (
                    der['power_per_unit_maximum']
                    * der['reactive_power_nominal']
                    * pd.Series(1.0, index=self.active_power_nominal_timeseries.index, name='reactive_power')
                )
            ], axis='columns')
        )
        self.output_minimum_timeseries = (
            pd.concat([
                pd.Series(0.0, index=self.active_power_nominal_timeseries.index, name='state_of_charge'),
                pd.Series(0.0, index=self.active_power_nominal_timeseries.index, name='active_power_charge'),
                pd.Series(0.0, index=self.active_power_nominal_timeseries.index, name='active_power_discharge'),
                (
                    der['power_per_unit_minimum']
                    * der['active_power_nominal']
                    * pd.Series(1.0, index=self.active_power_nominal_timeseries.index, name='active_power')
                ),
                (
                    der['power_per_unit_minimum']
                    * der['reactive_power_nominal']
                    * pd.Series(1.0, index=self.active_power_nominal_timeseries.index, name='reactive_power')
                )
            ], axis='columns')
        )


class FlexibleBuildingModel(FlexibleDERModel):
    """Flexible load model object."""

    der_type = 'flexible_building'

    power_factor_nominal: np.float
    is_electric_grid_connected: np.bool
    is_thermal_grid_connected: np.bool

    def __init__(
            self,
            der_data: fledge.data_interface.DERData,
            der_name: str
    ):
        """Construct flexible building model object by `der_data` and `der_name`."""

        # Store DER name.
        self.der_name = der_name

        # Get shorthand for DER data.
        der = der_data.ders.loc[self.der_name, :]

        # Obtain grid connection flags.
        self.is_electric_grid_connected = pd.notnull(der.at['electric_grid_name'])
        self.is_thermal_grid_connected = pd.notnull(der.at['thermal_grid_name'])

        # Store timesteps.
        self.timesteps = der_data.scenario_data.timesteps

        # Obtain CoBMo building model.
        flexible_building_model = (
            fledge.utils.get_building_model(
                der.at['der_model_name'],
                timestep_start=der_data.scenario_data.scenario.at['timestep_start'],
                timestep_end=der_data.scenario_data.scenario.at['timestep_end'],
                timestep_interval=der_data.scenario_data.scenario.at['timestep_interval'],
                connect_electric_grid=self.is_electric_grid_connected,
                connect_thermal_grid_cooling=self.is_thermal_grid_connected
            )
        )

        # Obtain nominal power factor.
        if self.is_electric_grid_connected:
            self.power_factor_nominal = (
                np.cos(np.arctan(
                    der.at['reactive_power_nominal']
                    / der.at['active_power_nominal']
                ))
                if ((der.at['active_power_nominal'] != 0.0) and (der.at['reactive_power_nominal'] != 0.0))
                else 1.0
            )

        # TODO: Obtain proper nominal timseries for CoBMo models.

        # Construct nominal active and reactive power timeseries.
        self.active_power_nominal_timeseries = (
            pd.Series(1.0, index=self.timesteps, name='active_power')
            * (
                der.at['active_power_nominal']
                if pd.notnull(der.at['active_power_nominal'])
                else 0.0
            )
        )
        self.reactive_power_nominal_timeseries = (
            pd.Series(1.0, index=self.timesteps, name='reactive_power')
            * (
                der.at['reactive_power_nominal']
                if pd.notnull(der.at['reactive_power_nominal'])
                else 0.0
            )
        )

        # Construct nominal thermal power timeseries.
        self.thermal_power_nominal_timeseries = (
            pd.Series(1.0, index=self.timesteps, name='thermal_power')
            * (
                der.at['thermal_power_nominal']
                if pd.notnull(der.at['thermal_power_nominal'])
                else 0.0
            )
        )

        # Obtain indexes.
        self.states = flexible_building_model.states
        self.controls = flexible_building_model.controls
        self.disturbances = flexible_building_model.disturbances
        self.outputs = flexible_building_model.outputs

        # Obtain initial state.
        self.state_vector_initial = flexible_building_model.state_vector_initial

        # Obtain state space matrices.
        self.state_matrix = flexible_building_model.state_matrix
        self.control_matrix = flexible_building_model.control_matrix
        self.disturbance_matrix = flexible_building_model.disturbance_matrix
        self.state_output_matrix = flexible_building_model.state_output_matrix
        self.control_output_matrix = flexible_building_model.control_output_matrix
        self.disturbance_output_matrix = flexible_building_model.disturbance_output_matrix

        # Instantiate disturbance timeseries.
        self.disturbance_timeseries = flexible_building_model.disturbance_timeseries

        # Obtain output constraint timeseries.
        self.output_maximum_timeseries = flexible_building_model.output_constraint_timeseries_maximum
        self.output_minimum_timeseries = flexible_building_model.output_constraint_timeseries_minimum

    def define_optimization_variables_bids(
            self,
            optimization_problem: pyo.ConcreteModel,
    ):
        optimization_problem.variable_z = pyo.Var(domain=pyo.NonNegativeReals)
        optimization_problem.variable_gamma = pyo.Var(domain=pyo.NonNegativeReals)
        optimization_problem.variable_q = pyo.Var(self.timesteps, [self.der_name], domain=pyo.NonNegativeReals)
        optimization_problem.variable_y = pyo.Var(self.timesteps, [self.der_name], domain=pyo.NonNegativeReals)


    def define_optimization_constraints_bids(
            self,
            optimization_problem: pyo.ConcreteModel,
            current_timestep,
            price_forecast: pd.DataFrame,
            actual_dispatch: pd.Series
    ):
        if optimization_problem.find_component('der_model_constraints') is None:
            optimization_problem.der_model_constraints = pyo.ConstraintList()

        # optimization_problem.der_model_constraints.add(
        #     optimization_problem.variable_gamma == len(self.timesteps[self.timesteps > current_timestep])
        # )

        for timestep in self.timesteps:
            if timestep < current_timestep:
                optimization_problem.der_model_constraints.add(
                    optimization_problem.output_vector[timestep, self.der_name, 'grid_electric_power'] <=
                    actual_dispatch[timestep] + 1
                )
            # elif timestep == current_timestep:
            #     continue
            # else:
            #     optimization_problem.der_model_constraints.add(
            #         optimization_problem.variable_z + optimization_problem.variable_q[timestep, self.der_name]
            #         >=
            #         ((price_forecast.at[timestep, 'upper_limit'] - price_forecast.at[
            #             timestep, 'lower_limit'])
            #          * optimization_problem.variable_y[timestep, self.der_name])
            #     )
            #     optimization_problem.der_model_constraints.add(
            #         (optimization_problem.output_vector[timestep, self.der_name, 'grid_electric_power']
            #          * 1800 / 3600.0 / 1000.0)
            #         <=
            #         optimization_problem.variable_y[timestep, self.der_name]
            #     )

    def define_optimization_objective_bids(
            self,
            optimization_problem: pyo.ConcreteModel,
            current_timestep,
            current_price: float,
            price_forecast: pd.DataFrame
    ):
        if optimization_problem.find_component('objective') is None:
            optimization_problem.objective = pyo.Objective(expr=0.0, sense=pyo.minimize)

        optimization_problem.objective.expr += (
            sum(
                price_forecast.at[timestep, 'expected_price']
                * optimization_problem.output_vector[timestep, self.der_name, 'grid_electric_power']
                * 1800 / 3600.0 / 1000.0
                # + optimization_problem.variable_q[timestep, self.der_name]
                for timestep in self.timesteps if timestep > current_timestep
            )
        )
        optimization_problem.objective.expr += (
                current_price
                * optimization_problem.output_vector[current_timestep, self.der_name, 'grid_electric_power']
                * 1800 / 3600.0 / 1000.0
                # + optimization_problem.variable_gamma * optimization_problem.variable_z
        )


class CoolingPlantModel(FlexibleDERModel):
    """Cooling plant model object."""

    der_type = 'cooling_plant'
    cooling_plant_efficiency: np.float

    def __init__(
            self,
            der_data: fledge.data_interface.DERData,
            der_name: str
    ):
        """Construct flexible load model object by `der_data` and `der_name`."""

        # Store DER name.
        self.der_name = der_name

        # Get shorthand for DER data.
        der = der_data.ders.loc[self.der_name, :].copy()
        der = pd.concat([der, der_data.der_definitions[der.at['definition_index']]])

        # Obtain grid connection flags.
        self.is_electric_grid_connected = pd.notnull(der.at['electric_grid_name'])
        self.is_thermal_grid_connected = pd.notnull(der.at['thermal_grid_name'])
        # Cooling plant must be connected to both thermal grid and electric grid.
        try:
            assert self.is_electric_grid_connected and self.is_thermal_grid_connected
        except AssertionError:
            logger.error(f"Cooling plant '{self.der_name}' must be connected to both thermal grid and electric grid")
            raise

        # Obtain cooling plant efficiency.
        # TODO: Enable consideration for dynamic wet bulb temperature.
        ambient_air_wet_bulb_temperature = (
            der.at['cooling_tower_set_reference_temperature_wet_bulb']
        )
        condensation_temperature = (
            der.at['cooling_tower_set_reference_temperature_condenser_water']
            + (
                der.at['cooling_tower_set_reference_temperature_slope']
                * (
                    ambient_air_wet_bulb_temperature
                    - der.at['cooling_tower_set_reference_temperature_wet_bulb']
                )
            )
            + der.at['condenser_water_temperature_difference']
            + der.at['chiller_set_condenser_minimum_temperature_difference']
            + 273.15
        )
        chiller_inverse_coefficient_of_performance = (
            (
                (
                    condensation_temperature
                    / der.at['chiller_set_evaporation_temperature']
                )
                - 1.0
            )
            * (
                der.at['chiller_set_beta']
                + 1.0
            )
        )
        evaporator_pump_specific_electric_power = (
            (1.0 / der.at['plant_pump_efficiency'])
            * scipy.constants.value('standard acceleration of gravity')
            * der.at['water_density']
            * der.at['evaporator_pump_head']
            / (
                der.at['water_density']
                * der.at['enthalpy_difference_distribution_water']
            )
        )
        condenser_specific_thermal_power = (
            1.0 + chiller_inverse_coefficient_of_performance
        )
        condenser_pump_specific_electric_power = (
            (1.0 / der.at['plant_pump_efficiency'])
            * scipy.constants.value('standard acceleration of gravity')
            * der.at['water_density']
            * der.at['condenser_pump_head']
            * condenser_specific_thermal_power
            / (
                der.at['water_density']
                * der.at['condenser_water_enthalpy_difference']
            )
        )
        cooling_tower_ventilation_specific_electric_power = (
            der.at['cooling_tower_set_ventilation_factor']
            * condenser_specific_thermal_power
        )
        self.cooling_plant_efficiency = (
            1.0 / sum([
                chiller_inverse_coefficient_of_performance,
                evaporator_pump_specific_electric_power,
                condenser_pump_specific_electric_power,
                cooling_tower_ventilation_specific_electric_power
            ])
        )

        # Store timesteps index.
        self.timesteps = der_data.scenario_data.timesteps

        # Construct active and reactive power timeseries.
        self.active_power_nominal_timeseries = (
            pd.Series(0.0, index=self.timesteps, name='active_power')
        )
        self.reactive_power_nominal_timeseries = (
            pd.Series(0.0, index=self.timesteps, name='reactive_power')
        )

        # Construct nominal thermal power timeseries.
        self.thermal_power_nominal_timeseries = (
            pd.Series(0.0, index=self.timesteps, name='thermal_power')
        )

        # Instantiate indexes.
        self.states = pd.Index([])
        self.controls = pd.Index(['active_power'])
        self.disturbances = pd.Index([])
        self.outputs = pd.Index(['active_power', 'reactive_power', 'thermal_power'])

        # Instantiate initial state.
        self.state_vector_initial = (
            pd.Series(0.0, index=self.states)
        )

        # Instantiate state space matrices.
        self.state_matrix = (
            pd.DataFrame(0.0, index=self.states, columns=self.states)
        )
        self.control_matrix = (
            pd.DataFrame(0.0, index=self.states, columns=self.controls)
        )
        self.disturbance_matrix = (
            pd.DataFrame(0.0, index=self.states, columns=self.disturbances)
        )
        self.state_output_matrix = (
            pd.DataFrame(0.0, index=self.outputs, columns=self.states)
        )
        self.control_output_matrix = (
            pd.DataFrame(0.0, index=self.outputs, columns=self.controls)
        )
        self.control_output_matrix.at['active_power', 'active_power'] = 1.0
        self.control_output_matrix.at['reactive_power', 'active_power'] = (
            der.at['reactive_power_nominal'] / der.at['active_power_nominal']
            if der.at['active_power_nominal'] != 0.0
            else 0.0
        )
        self.control_output_matrix.at['thermal_power', 'active_power'] = -1.0 * self.cooling_plant_efficiency
        self.disturbance_output_matrix = (
            pd.DataFrame(0.0, index=self.outputs, columns=self.disturbances)
        )

        # Instantiate disturbance timeseries.
        self.disturbance_timeseries = (
            pd.DataFrame(0.0, index=self.timesteps, columns=self.disturbances)
        )

        # Construct output constraint timeseries
        self.output_maximum_timeseries = (
            pd.DataFrame(
                [[0.0, 0.0, der.at['thermal_power_nominal']]],
                index=self.timesteps,
                columns=self.outputs
            )
        )
        self.output_minimum_timeseries = (
            pd.DataFrame(
                [[der.at['active_power_nominal'], der.at['reactive_power_nominal'], 0.0]],
                index=self.timesteps,
                columns=self.outputs
            )
        )


class DERModelSet(object):
    """DER model set object."""

    timesteps: pd.Index
    der_names: pd.Index
    fixed_der_names: pd.Index
    flexible_der_names: pd.Index
    der_models: typing.Dict[str, typing.Union[DERModel, FixedDERModel, FlexibleDERModel]]
    fixed_der_models: typing.Dict[str, FixedDERModel]
    flexible_der_models: typing.Dict[str, FlexibleDERModel]
    states: pd.Index
    controls: pd.Index
    outputs: pd.Index

    def __init__(
            self,
            scenario_name: str
    ):

        # Obtain data.
        scenario_data = fledge.data_interface.ScenarioData(scenario_name)
        der_data = fledge.data_interface.DERData(scenario_name)

        # Obtain timesteps.
        self.timesteps = scenario_data.timesteps

        # Obtain DER names.
        self.der_names = der_data.ders.index

        # Obtain DER models.
        # TODO: Fix cobmo error when running as parallel starmap.
        self.der_models = (
            dict(zip(
                self.der_names,
                itertools.starmap(
                    make_der_model,
                    zip(itertools.repeat(der_data), self.der_names.to_list())
                )
            ))
        )

        # Obtain fixed / flexible DER name / models.
        self.fixed_der_names = list()
        self.flexible_der_names = list()
        self.fixed_der_models = dict()
        self.flexible_der_models = dict()
        for der_name in self.der_names:
            if isinstance(self.der_models[der_name], FixedDERModel):
                self.fixed_der_names.append(der_name)
                self.fixed_der_models[der_name] = self.der_models[der_name]
            elif isinstance(self.der_models[der_name], FlexibleDERModel):
                self.flexible_der_names.append(der_name)
                self.flexible_der_models[der_name] = self.der_models[der_name]
            else:
                # Raise error, if DER model object is neither fixed nor flexible DER model.
                logger.error(
                    f"DER model class `{type(self.der_models[der_name])}` for DER '{der_name}' "
                    f"is not a subclass of `FixedDERModel` or `FlexibleDERModel`."
                )
        self.fixed_der_names = pd.Index(self.fixed_der_names)
        self.flexible_der_names = pd.Index(self.flexible_der_names)

        # Obtain flexible DER state space indexes.
        self.states = (
            pd.MultiIndex.from_tuples([
                (der_name, state)
                for der_name in self.flexible_der_names
                for state in self.flexible_der_models[der_name].states
            ])
            if len(self.flexible_der_names) > 0 else pd.Index([])
        )
        self.controls = (
            pd.MultiIndex.from_tuples([
                (der_name, control)
                for der_name in self.flexible_der_names
                for control in self.flexible_der_models[der_name].controls
            ])
            if len(self.flexible_der_names) > 0 else pd.Index([])
        )
        self.outputs = (
            pd.MultiIndex.from_tuples([
                (der_name, output)
                for der_name in self.flexible_der_names
                for output in self.flexible_der_models[der_name].outputs
            ])
            if len(self.flexible_der_names) > 0 else pd.Index([])
        )

    def define_optimization_variables(
            self,
            optimization_problem: pyo.ConcreteModel
    ):

        # Define flexible DER variables.
        optimization_problem.state_vector = pyo.Var(self.timesteps, self.states.tolist())
        optimization_problem.control_vector = pyo.Var(self.timesteps, self.controls.tolist())
        optimization_problem.output_vector = pyo.Var(self.timesteps, self.outputs.tolist())
        # optimization_problem.peak_load = pyo.Var(domain=pyo.NonNegativeReals)

    def define_optimization_constraints(
            self,
            optimization_problem: pyo.ConcreteModel,
            electric_grid_model: fledge.electric_grid_models.ElectricGridModelDefault = None,
            power_flow_solution: fledge.electric_grid_models.PowerFlowSolution = None,
            thermal_grid_model: fledge.thermal_grid_models.ThermalGridModel = None,
            thermal_power_flow_solution: fledge.thermal_grid_models.ThermalPowerFlowSolution = None
    ):

        # Define DER constraints for each DER.
        for der_name in self.der_names:
            self.der_models[der_name].define_optimization_constraints(
                optimization_problem,
                electric_grid_model,
                power_flow_solution,
                thermal_grid_model,
                thermal_power_flow_solution
            )

    @multimethod
    def define_optimization_objective(
            self,
            optimization_problem: pyo.ConcreteModel,
            price_data: fledge.data_interface.PriceData,
            electric_grid_model: fledge.electric_grid_models.ElectricGridModelDefault = None,
            thermal_grid_model: fledge.thermal_grid_models.ThermalGridModel = None,
    ):

        # Define objective for each DER.
        for der_name in self.der_names:
            self.der_models[der_name].define_optimization_objective(
                optimization_problem,
                price_data,
                electric_grid_model,
                thermal_grid_model
            )

    @multimethod
    def define_optimization_objective(
            self,
            optimization_problem: pyo.ConcreteModel,
            price_timeseries: pd.DataFrame,
            current_timestep: pd.Timestamp
    ):

        if optimization_problem.find_component('objective') is None:
            optimization_problem.objective = pyo.Objective(expr=0.0, sense=pyo.minimize)

        optimization_problem.objective.expr += (
                sum(price_timeseries.loc[timestep, 'price_value']
                    * optimization_problem.output_vector[timestep, der_name, 'grid_electric_power']
                    for der_name in self.der_names for timestep in self.timesteps if timestep != current_timestep
                    )
        )
        optimization_problem.objective.expr += (
            sum(
                price_timeseries.loc[current_timestep, 'lower_limit']
                * optimization_problem.output_vector[current_timestep, der_name, 'grid_electric_power']
                for der_name in self.der_names
            )
        )


    @multimethod
    def define_optimization_objective(
        self,
        optimization_problem: pyo.ConcreteModel,
        pv_generation: pd.Series,
        residual_demand: pd.Series
    ):

        if optimization_problem.find_component('objective') is None:
            optimization_problem.objective = pyo.Objective(expr=0.0, sense=pyo.minimize)

        for timestep in self.timesteps:
            optimization_problem.objective.expr += (
                    (14.198
                    + 0.0139 * (sum(
                                optimization_problem.output_vector[timestep, der_name, 'grid_electric_power']
                                for der_name in self.der_names) / 1e6
                                + residual_demand.loc[timestep] - pv_generation.loc[timestep]/1e3))  # aggregate demand in MW
                                * (sum(
                                optimization_problem.output_vector[timestep, der_name, 'grid_electric_power']
                                for der_name in self.der_names
                                ) / 1e6)
            )

    def get_optimization_results(
            self,
            optimization_problem: pyo.ConcreteModel
    ) -> fledge.data_interface.ResultsDict:

        # Instantiate results variables.
        state_vector = pd.DataFrame(0.0, index=self.timesteps, columns=self.states)
        control_vector = pd.DataFrame(0.0, index=self.timesteps, columns=self.controls)
        output_vector = pd.DataFrame(0.0, index=self.timesteps, columns=self.outputs)

        # Solve optimization problem
        solver = pyo.SolverFactory('gurobi')
        solver.solve(optimization_problem)

        # Obtain results.
        for timestep in self.timesteps:
            for state in self.states:
                state_vector.at[timestep, state] = (
                    optimization_problem.state_vector[timestep, state].value
                )
            for control in self.controls:
                control_vector.at[timestep, control] = (
                    optimization_problem.control_vector[timestep, control].value
                )
            for output in self.outputs:
                output_vector.at[timestep, output] = (
                    optimization_problem.output_vector[timestep, output].value
                )

        return fledge.data_interface.ResultsDict(
            state_vector=state_vector,
            control_vector=control_vector,
            output_vector=output_vector
        )


def make_der_model(
    der_data: fledge.data_interface.DERData,
    der_name: str
) -> DERModel:
    """Factory method for DER models, makes appropriate DER model type for given `der_name`."""

    # Obtain DER type.
    der_type = der_data.ders.loc[der_name, 'der_type']

    # Obtain DER model classes.
    der_model_classes = (
        inspect.getmembers(sys.modules[__name__], lambda cls: inspect.isclass(cls) and issubclass(cls, DERModel))
    )

    # Obtain DER model for given `der_type`.
    for der_model_class_name, der_model_class in der_model_classes:
        if der_type == der_model_class.der_type:
            return der_model_class(der_data, der_name)

    # Raise error, if no DER model class found for given `der_type`.
    logger.error(
        f"Can't find DER model class for DER '{der_name}' of type '{der_type}'. "
        f"Please check if valid `der_type` is defined."
    )
    raise ValueError
