from typing import TYPE_CHECKING

from cython_extensions import cy_unit_pending
from sc2.ids.upgrade_id import UpgradeId

from ares import ManagerMediator, UnitRole
from ares.behaviors.macro import (
    AutoSupply,
    BuildWorkers,
    ExpansionController,
    GasBuildingController,
    MacroPlan,
    Mining,
    ProductionController,
    SpawnController,
)
from ares.behaviors.macro.upgrade_controller import UpgradeController
from ares.managers.manager import Manager
from sc2.data import Race
from sc2.ids.unit_typeid import UnitTypeId as UnitID
from sc2.position import Point2
from sc2.units import Units

from bot.managers.deimos_mediator import DeimosMediator

if TYPE_CHECKING:
    from ares import AresBot


class MacroManager(Manager):
    deimos_mediator: DeimosMediator

    def __init__(
        self,
        ai: "AresBot",
        config: dict,
        mediator: ManagerMediator,
    ) -> None:
        """Handle all Phoenix harass.

        Parameters
        ----------
        ai :
            Bot object that will be running the game
        config :
            Dictionary with the data from the configuration file
        mediator :
            ManagerMediator used for getting information from other managers.
        """
        super().__init__(ai, config, mediator)

        self._main_building_location: Point2 = self.ai.start_location
        self._workers_per_gas: int = 3
        self._on_gas_toggle: bool = True

    @property
    def can_expand(self) -> bool:
        if (
            self.deimos_mediator.get_enemy_rushed
            or len(self.manager_mediator.get_enemy_army_dict[UnitID.REAPER]) >= 2
        ) and self.ai.supply_army < 22:
            return False

        return self.ai.minerals > 400

    @property
    def gas_buildings_required(self) -> int:
        supply_workers: float = self.ai.supply_workers
        if supply_workers < 20.0:
            return 0

        gas_required: int = 3 if supply_workers < 40 else 100

        return gas_required

    @property
    def max_probes(self) -> int:
        max_probes: int = min(90, 22 * len(self.ai.townhalls))

        if self.manager_mediator.get_enemy_ling_rushed and self.ai.supply_army < 28:
            max_probes = 22
        elif not self.manager_mediator.get_enemy_expanded and self.ai.supply_army < 28:
            if self.deimos_mediator.get_enemy_rushed:
                max_probes = 25
            elif self.ai.enemy_race == Race.Protoss:
                max_probes = 29

        return max_probes

    @property
    def upgrades_enabled(self) -> bool:
        return len(self.ai.gas_buildings) >= 4 and self.ai.supply_army > 20

    @property
    def upgrade_list(self) -> list[UpgradeId]:
        desired_upgrades: list[UpgradeId] = [
            UpgradeId.WARPGATERESEARCH,
            UpgradeId.PROTOSSGROUNDWEAPONSLEVEL1,
            UpgradeId.PROTOSSGROUNDARMORSLEVEL1,
            UpgradeId.PROTOSSGROUNDWEAPONSLEVEL2,
            UpgradeId.PROTOSSGROUNDARMORSLEVEL2,
            UpgradeId.PROTOSSGROUNDWEAPONSLEVEL3,
            UpgradeId.PROTOSSGROUNDARMORSLEVEL3,
        ]

        if self.manager_mediator.get_own_army_dict[UnitID.TEMPEST] or cy_unit_pending(
            self.ai, UnitID.TEMPEST
        ):
            desired_upgrades.extend(
                [
                    UpgradeId.TEMPESTGROUNDATTACKUPGRADE,
                    UpgradeId.PROTOSSAIRWEAPONSLEVEL1,
                    UpgradeId.PROTOSSAIRARMORSLEVEL1,
                    UpgradeId.PROTOSSAIRWEAPONSLEVEL2,
                    UpgradeId.PROTOSSAIRARMORSLEVEL2,
                    UpgradeId.PROTOSSAIRWEAPONSLEVEL3,
                    UpgradeId.PROTOSSAIRARMORSLEVEL3,
                ]
            )

        if self.manager_mediator.get_own_army_dict[UnitID.COLOSSUS] or cy_unit_pending(
            self.ai, UnitID.COLOSSUS
        ):
            desired_upgrades.append(UpgradeId.EXTENDEDTHERMALLANCE)

        return desired_upgrades

    @property
    def require_observer(self) -> bool:
        if (
            self.deimos_mediator.get_enemy_rushed
            and self.ai.time < 330.0
            and len(self.manager_mediator.get_enemy_army_dict[UnitID.BANSHEE]) == 0
        ):
            return False

        observers_required: int = 1 if self.ai.supply_used < 138 else 2
        return (
            len(self.manager_mediator.get_own_army_dict[UnitID.OBSERVER])
            + self.ai.unit_pending(UnitID.OBSERVER)
            < observers_required
        )

    @property
    def require_phoenix(self) -> bool:
        return (
            self.ai.build_order_runner.chosen_opening == "PhoenixEconomic"
            and self.ai.supply_used < 90
            and len(self.manager_mediator.get_own_army_dict[UnitID.PHOENIX]) < 8
            and len(self.manager_mediator.get_enemy_army_dict[UnitID.VIKINGFIGHTER]) < 2
            and len(self.manager_mediator.get_enemy_army_dict[UnitID.REAPER]) < 3
            and (
                len(self.manager_mediator.get_enemy_army_dict[UnitID.MARINE]) < 6
                and self.ai.supply_army < 32
                and not self.ai.enemy_structures(UnitID.FACTORYTECHLAB)
            )
        )

    async def update(self, iteration: int) -> None:
        if iteration % 16 == 0:
            self._check_building_location()

        self._do_mining()

        if self.ai.build_order_runner.build_completed:
            macro_plan: MacroPlan = MacroPlan()
            if self.upgrades_enabled:
                macro_plan.add(
                    UpgradeController(
                        self.upgrade_list, base_location=self._main_building_location
                    )
                )
            macro_plan.add(AutoSupply(self._main_building_location))
            macro_plan.add(BuildWorkers(self.max_probes))
            if self.require_observer:
                macro_plan.add(
                    SpawnController(
                        {UnitID.OBSERVER: {"proportion": 1.0, "priority": 0}},
                    )
                )
            if self.require_phoenix:
                macro_plan.add(
                    SpawnController(
                        {UnitID.PHOENIX: {"proportion": 1.0, "priority": 0}}
                    )
                )
            macro_plan.add(
                SpawnController(
                    self.deimos_mediator.get_army_comp,
                    spawn_target=self.manager_mediator.get_own_nat,
                    freeflow_mode=self.ai.minerals > 500 and self.ai.vespene > 500,
                    ignore_proportions_below_unit_count=11,
                )
            )
            if self.can_expand:
                max_pending = 2 if self.ai.supply_used > 138 else 1
                macro_plan.add(
                    ExpansionController(to_count=100, max_pending=max_pending)
                )
            if self._workers_per_gas > 0:
                macro_plan.add(
                    GasBuildingController(
                        to_count=self.gas_buildings_required,
                        max_pending=1 if self.ai.supply_workers < 60 else 3,
                    )
                )

            add_production_at_bank: tuple = (300, 300)
            alpha: float = 0.6
            if self.deimos_mediator.get_enemy_rushed:
                add_production_at_bank = (150, 0)
                alpha = 0.4
            elif UnitID.TEMPEST in self.deimos_mediator.get_army_comp:
                alpha = 1.0
            macro_plan.add(
                ProductionController(
                    self.deimos_mediator.get_army_comp,
                    base_location=self._main_building_location,
                    add_production_at_bank=add_production_at_bank,
                    alpha=alpha,
                )
            )

            self.ai.register_behavior(macro_plan)

    def _do_mining(self):
        gatherers: Units = self.manager_mediator.get_units_from_role(
            role=UnitRole.GATHERING
        )
        if (
            (
                self.manager_mediator.get_enemy_worker_rushed
                and len(gatherers) < 21
                and self.ai.time < 210.0
            )
            or len(gatherers) < 12
            or (
                self.ai.minerals < 100
                and self.ai.vespene > 300
                and self.ai.supply_used < 84
            )
        ):
            self._workers_per_gas = 0
        elif self.ai.vespene < 100:
            self._workers_per_gas = 3

        if self._on_gas_toggle and self.ai.vespene > 1500 and self.ai.minerals < 100:
            self._on_gas_toggle = False
        elif self.ai.vespene < 400 or self.ai.minerals > 1200:
            self._on_gas_toggle = True
            self._workers_per_gas = 3

        if not self._on_gas_toggle:
            self._workers_per_gas = 0
        self.ai.register_behavior(Mining(workers_per_gas=self._workers_per_gas))

    def _check_building_location(self):
        if self.ai.time > 540.0 and self.ai.ready_townhalls:
            self._main_building_location = self.ai.ready_townhalls.furthest_to(
                self.ai.start_location
            ).position
