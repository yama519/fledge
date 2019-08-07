# Application programming interface tests.

Test.@testset "API tests" begin
    Test.@testset "Get electric grid data test" begin
        # Define expected result.
        # - TODO: Replace type test with proper result check.
        expected = FLEDGE.DatabaseInterface.ElectricGridData

        # Get actual result.
        @time_log "Get electric grid data test" actual = (
            typeof(FLEDGE.API.get_electric_grid_data(test_scenario_name))
        )

        # Evaluate test.
        Test.@test actual == expected
    end

    Test.@testset "Get electric grid model test" begin
        # Define expected result.
        # - TODO: Replace type test with proper result check.
        expected = FLEDGE.ElectricGridModels.ElectricGridModel

        # Get actual result.
        @time_log "Get electric grid model test" actual = (
            typeof(FLEDGE.API.get_electric_grid_model(test_scenario_name))
        )

        # Evaluate test.
        Test.@test actual == expected
    end

    Test.@testset "Get linear electric grid model test" begin
        # Define expected result.
        # - TODO: Replace type test with proper result check.
        expected = FLEDGE.ElectricGridModels.LinearElectricGridModel

        # Get actual result.
        @time_log "Get linear electric grid model test" actual = (
            typeof(
                FLEDGE.API.get_linear_electric_grid_model(test_scenario_name)
            )
        )

        # Evaluate test.
        Test.@test actual == expected
    end

    Test.@testset "Run operation problem test" begin
        # Define expected result.
        expected = true

        # Get actual result.
        @time_log "Run operation problem test" actual = (
            FLEDGE.API.run_operation_problem(test_scenario_name)
        )

        # Evaluate test.
        Test.@test actual == expected
    end

    Test.@testset "Initialize OpenDSS model test" begin
        # Define expected result.
        electric_grid_data = (
            FLEDGE.API.get_electric_grid_data(test_scenario_name)
        )
        expected = electric_grid_data.electric_grids[:electric_grid_name][1]

        # Get actual result.
        @time_log "Initialize OpenDSS model test" (
            FLEDGE.API.initialize_open_dss_model(test_scenario_name)
        )
        actual = OpenDSSDirect.Circuit.Name()

        # Evaluate test.
        Test.@test actual == expected
    end
end
