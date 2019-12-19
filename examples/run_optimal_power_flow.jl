"Example script for setting up and solving an optimal power flow problem."

import FLEDGE

import CPLEX # `]add CPLEX` to use CPLEX.
import GLPK
import Gurobi # `]add Gurobi` to use Gurobi.
import GR
import JuMP
import Logging
import Plots
import Statistics
import TimeSeries

# Settings.
scenario_name = "singapore_6node"
Plots.gr()  # Select plotting backend.

# Get model.
electric_grid_data = (
    FLEDGE.DatabaseInterface.ElectricGridData(scenario_name)
)
electric_grid_index = (
    FLEDGE.ElectricGridModels.ElectricGridIndex(scenario_name)
)
electric_grid_model = (
    FLEDGE.ElectricGridModels.ElectricGridModel(scenario_name)
)
power_flow_solution = (
    FLEDGE.PowerFlowSolvers.PowerFlowSolutionFixedPoint(scenario_name)
)
linear_electric_grid_model = (
    FLEDGE.ElectricGridModels.LinearElectricGridModel(scenario_name)
)

# Instantiate optimization problem.
optimization_problem = (
    # JuMP.Model(JuMP.with_optimizer(CPLEX.Optimizer))
    JuMP.Model(JuMP.with_optimizer(GLPK.Optimizer, msg_lev=GLPK.MSG_ON))
    # JuMP.Model(JuMP.with_optimizer(Gurobi.Optimizer))
)

# Define variables.

# Load.
JuMP.@variable(
    optimization_problem,
    load_active_power_vector_change[electric_grid_index.load_names]
)
JuMP.@variable(
    optimization_problem,
    load_reactive_power_vector_change[electric_grid_index.load_names]
)

# Power.
JuMP.@variable(
    optimization_problem,
    node_power_vector_wye_active_change[electric_grid_index.nodes_phases]
)
JuMP.@variable(
    optimization_problem,
    node_power_vector_wye_reactive_change[electric_grid_index.nodes_phases]
)
JuMP.@variable(
    optimization_problem,
    node_power_vector_delta_active_change[electric_grid_index.nodes_phases]
)
JuMP.@variable(
    optimization_problem,
    node_power_vector_delta_reactive_change[electric_grid_index.nodes_phases]
)

# Voltage.
JuMP.@variable(
    optimization_problem,
    voltage_magnitude_vector_change[electric_grid_index.nodes_phases]
)

# Branch flows.
JuMP.@variable(
    optimization_problem,
    branch_power_vector_1_squared_change[electric_grid_index.branches_phases]
)
JuMP.@variable(
    optimization_problem,
    branch_power_vector_2_squared_change[electric_grid_index.branches_phases]
)

# Loss.
JuMP.@variable(
    optimization_problem,
    loss_active_change
)
JuMP.@variable(
    optimization_problem,
    loss_reactive_change
)

# Trust-region.
JuMP.@variable(
    optimization_problem,
    change_limit
)

# Define constraints.

# Load.
JuMP.@constraint(
    optimization_problem,
    load_active_minimum_maximum,
    (
        -0.5 .* real.(electric_grid_model.load_power_vector_nominal)
        .<=
        load_active_power_vector_change.data
        .<=
        0.5 .* real.(electric_grid_model.load_power_vector_nominal)
    )
)
JuMP.@constraint(
    optimization_problem,
    load_reactive_minimum_maximum,
    (
        -0.5 .* imag.(electric_grid_model.load_power_vector_nominal)
        .<=
        load_reactive_power_vector_change.data
        .<=
        0.5 .* imag.(electric_grid_model.load_power_vector_nominal)
    )
)

# Power.
JuMP.@constraint(
    optimization_problem,
    node_power_vector_wye_active_equation,
    (
        node_power_vector_wye_active_change.data
        .==
        0.0
        + electric_grid_model.load_incidence_wye_matrix
        * (-1.0 .* load_active_power_vector_change.data)
    )
)
JuMP.@constraint(
    optimization_problem,
    node_power_vector_wye_reactive_equation,
    (
        node_power_vector_wye_reactive_change.data
        .==
        0.0
        + electric_grid_model.load_incidence_wye_matrix
        * (-1.0 .* load_reactive_power_vector_change.data)
    )
)
JuMP.@constraint(
    optimization_problem,
    node_power_vector_delta_active_equation,
    (
        node_power_vector_delta_active_change.data
        .==
        0.0
        + electric_grid_model.load_incidence_delta_matrix
        * (-1.0 .* load_active_power_vector_change.data)
    )
)
JuMP.@constraint(
    optimization_problem,
    node_power_vector_delta_reactive_equation,
    (
        node_power_vector_delta_reactive_change.data
        .==
        0.0
        + electric_grid_model.load_incidence_delta_matrix
        * (-1.0 .* load_reactive_power_vector_change.data)
    )
)


# Voltage.
JuMP.@constraint(
    optimization_problem,
    voltage_magnitude_equation,
    (
        voltage_magnitude_vector_change.data
        .==
        (
            linear_electric_grid_model.sensitivity_voltage_magnitude_by_power_wye_active
            * node_power_vector_wye_active_change.data
            + linear_electric_grid_model.sensitivity_voltage_magnitude_by_power_wye_reactive
            * node_power_vector_wye_reactive_change.data
            + linear_electric_grid_model.sensitivity_voltage_magnitude_by_power_delta_active
            * node_power_vector_delta_active_change.data
            + linear_electric_grid_model.sensitivity_voltage_magnitude_by_power_delta_reactive
            * node_power_vector_delta_reactive_change.data
        )
    )
)

# Branch flows.
JuMP.@constraint(
    optimization_problem,
    branch_flow_1_equation,
    (
        branch_power_vector_1_squared_change.data
        .==
        (
            linear_electric_grid_model.sensitivity_branch_power_1_by_power_wye_active
            * node_power_vector_wye_active_change.data
            + linear_electric_grid_model.sensitivity_branch_power_1_by_power_wye_reactive
            * node_power_vector_wye_reactive_change.data
            + linear_electric_grid_model.sensitivity_branch_power_1_by_power_delta_active
            * node_power_vector_delta_active_change.data
            + linear_electric_grid_model.sensitivity_branch_power_1_by_power_delta_reactive
            * node_power_vector_delta_reactive_change.data
        )
    )
)
JuMP.@constraint(
    optimization_problem,
    branch_flow_2_equation,
    (
        branch_power_vector_2_squared_change.data
        .==
        (
            linear_electric_grid_model.sensitivity_branch_power_2_by_power_wye_active
            * node_power_vector_wye_active_change.data
            + linear_electric_grid_model.sensitivity_branch_power_2_by_power_wye_reactive
            * node_power_vector_wye_reactive_change.data
            + linear_electric_grid_model.sensitivity_branch_power_2_by_power_delta_active
            * node_power_vector_delta_active_change.data
            + linear_electric_grid_model.sensitivity_branch_power_2_by_power_delta_reactive
            * node_power_vector_delta_reactive_change.data
        )
    )
)

# Loss.
JuMP.@constraint(
    optimization_problem,
    loss_active_equation,
    (
        loss_active_change
        .==
        (
            linear_electric_grid_model.sensitivity_loss_active_by_power_wye_active
            * node_power_vector_wye_active_change.data
            + linear_electric_grid_model.sensitivity_loss_active_by_power_wye_reactive
            * node_power_vector_wye_reactive_change.data
            + linear_electric_grid_model.sensitivity_loss_active_by_power_delta_active
            * node_power_vector_delta_active_change.data
            + linear_electric_grid_model.sensitivity_loss_active_by_power_delta_reactive
            * node_power_vector_delta_reactive_change.data
        )
    )
)
JuMP.@constraint(
    optimization_problem,
    loss_reactive_equation,
    (
        loss_reactive_change
        .==
        (
            linear_electric_grid_model.sensitivity_loss_reactive_by_power_wye_active
            * node_power_vector_wye_active_change.data
            + linear_electric_grid_model.sensitivity_loss_reactive_by_power_wye_reactive
            * node_power_vector_wye_reactive_change.data
            + linear_electric_grid_model.sensitivity_loss_reactive_by_power_delta_active
            * node_power_vector_delta_active_change.data
            + linear_electric_grid_model.sensitivity_loss_reactive_by_power_delta_reactive
            * node_power_vector_delta_reactive_change.data
        )
    )
)

# Trust region.
JuMP.@constraint(
    optimization_problem,
    trust_region_voltage_magnitude_minimum,
    (
        -change_limit
        .<=
        voltage_magnitude_vector_change.data
    )
)
JuMP.@constraint(
    optimization_problem,
    trust_region_voltage_magnitude_maximum,
    (
        voltage_magnitude_vector_change.data
        .<=
        change_limit
    )
)
JuMP.@constraint(
    optimization_problem,
    trust_region_load_active_minimum,
    (
        -change_limit
        .<=
        load_active_power_vector_change.data
    )
)
JuMP.@constraint(
    optimization_problem,
    trust_region_load_active_maximum,
    (
        load_active_power_vector_change.data
        .<=
        change_limit
    )
)
JuMP.@constraint(
    optimization_problem,
    trust_region_load_reactive_minimum,
    (
        -change_limit
        .<=
        load_reactive_power_vector_change.data
    )
)
JuMP.@constraint(
    optimization_problem,
    trust_region_load_reactive_maximum,
    (
        load_reactive_power_vector_change.data
        .<=
        change_limit
    )
)

# Define objective.
JuMP.@objective(
    optimization_problem,
    Min,
    (
        + sum(load_active_power_vector_change.data)
        + sum(load_reactive_power_vector_change.data)
    )
)

# Solve optimization problem.
Logging.@info("", optimization_problem)
JuMP.optimize!(optimization_problem)

# Get results.
optimization_termination_status = JuMP.termination_status(optimization_problem)
Logging.@info("", optimization_termination_status)

# Voltage.
voltage_magnitude_vector_per_unit_value = (
    (
        JuMP.value.(voltage_magnitude_vector_change.data)
        + abs.(power_flow_solution.node_voltage_vector)
    )
    ./ abs.(electric_grid_model.node_voltage_vector_no_load)
)
Logging.@info("", Statistics.mean(voltage_magnitude_vector_per_unit_value))

# Load.
load_active_power_vector_per_unit_value = (
    (
        JuMP.value.(load_active_power_vector_change.data)
        + real.(electric_grid_model.load_power_vector_nominal)
    )
    ./ real.(electric_grid_model.load_power_vector_nominal)
)
Logging.@info("", Statistics.mean(load_active_power_vector_per_unit_value))

# Branch flows.
branch_power_vector_1_squared_per_unit_value = (
    (
        JuMP.value.(branch_power_vector_1_squared_change.data)
        + (abs.(power_flow_solution.branch_power_vector_1) .^ 2)
    )
    ./ (abs.(power_flow_solution.branch_power_vector_1) .^ 2)
)
Logging.@info("", Statistics.mean(branch_power_vector_1_squared_per_unit_value))

# Loss.
loss_active_per_unit_value = (
    (
        JuMP.value(loss_active_change)
        + real(power_flow_solution.loss)
    )
    / real(power_flow_solution.loss)
)
Logging.@info("", loss_active_per_unit_value)
