# backend/app/sim/

The simulation layer. `base.py` defines the seam that makes EnergyPlus
swappable; every other layer is written against it and cannot tell which
simulator is running.

| Module | Purpose | Milestone |
|---|---|---|
| `base.py` | `BuildingState`, `ControlAction`, `Simulator` protocol. No physics, no I/O. | 1 |
| `toy.py` | `ToySimulator` — single-zone RC thermal network in NumPy. | 1 |
| `energyplus.py` | `EnergyPlusSimulator` — live control through the EnergyPlus runtime Python API. | 4 |
| `weather.py` | `SyntheticWeather` and `EpwWeather` — outdoor conditions and occupancy. | 1 / 4 |

## Why there are two simulators

`ToySimulator` is a working building model, not a mock. It lets the loop, the
agents, persistence and the dashboard be built and stress-tested before
EnergyPlus is installed, and it is the fallback if the demonstration machine
cannot host EnergyPlus. Two genuine implementations are what justify the
protocol.

## Adding a third simulator

Implement `reset()`, `step(action)`, `close()`, and the `timestep_seconds` and
`horizon_steps` attributes. Nothing else in the codebase needs to change.

## EnergyPlus notes

`pyenergyplus` is **not** a PyPI package — it ships inside the EnergyPlus
installation. `Settings.energyplus_dir` points at that directory and
`energyplus.py` places it on `sys.path` before importing. Set-points reach the
running simulation through actuators inside a per-timestep callback; nothing is
achieved by rewriting `.idf` files between runs.
