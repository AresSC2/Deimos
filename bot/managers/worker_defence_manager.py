from typing import TYPE_CHECKING

from ares import ManagerMediator
from ares.consts import WORKER_TYPES, UnitRole, UnitTreeQueryType
from ares.managers.manager import Manager
from sc2.ids.unit_typeid import UnitTypeId as UnitID
from sc2.unit import Unit
from sc2.units import Units

from bot.combat.base_combat import BaseCombat
from bot.combat.worker_defenders import WorkerDefenders
from bot.managers.deimos_mediator import DeimosMediator
from cython_extensions import cy_center, cy_distance_to_squared

if TYPE_CHECKING:
    from ares import AresBot


class WorkerDefenceManager(Manager):
    deimos_mediator: DeimosMediator
    MIN_HEALTH_PERC: float = 0.34

    def __init__(
        self,
        ai: "AresBot",
        config: dict,
        mediator: ManagerMediator,
    ) -> None:
        """Handle scouting related tasks.

        This manager should calculate where and when to scout.
        Assign units, and call related combat classes to execute
        the scouting logic.

        Parameters
        ----------
        ai :
            Bot object that will be running the game
        config :
            Dictionary with the data from the configuration file
        mediator :
            ManagerMediator used for getting information from other managers.

        Returns
        -------

        """
        super().__init__(ai, config, mediator)

        self.worker_defenders_behavior: BaseCombat = WorkerDefenders(
            ai, config, mediator
        )

        self._enemy_to_workers_required: dict[UnitID, int] = {
            UnitID.DRONE: 1,
            UnitID.PROBE: 1,
            UnitID.MARINE: 0,
            UnitID.SCV: 1,
            UnitID.ZERGLING: 2,
            UnitID.ZEALOT: 4
        }

        self._proxy_to_workers_required: dict[UnitID, int] = {
            UnitID.PYLON: 4,
            UnitID.HATCHERY: 12,
            UnitID.PHOTONCANNON: 3,
            UnitID.COMMANDCENTER: 12,
            UnitID.BUNKER: 6,
        }

    @property
    def enabled(self) -> bool:
        return (
            self.ai.supply_army < 24
            and not self.manager_mediator.get_enemy_marine_rush
            and not [
                s
                for s in self.manager_mediator.get_own_structures_dict[
                    UnitID.PHOTONCANNON
                ]
                if s.is_ready
            ]
        )

    async def update(self, iteration: int) -> None:
        enemy_near_bases: dict[
            int, set[int]
        ] = self.manager_mediator.get_ground_enemy_near_bases
        defender_probes: Units = self.manager_mediator.get_units_from_role(
            role=UnitRole.BASE_DEFENDER, unit_type=UnitID.PROBE
        )
        num_enemy: int = 0
        if self.enabled:
            num_enemy = self._assign_worker_defenders(defender_probes, enemy_near_bases)

        self._unassign_worker_defenders(defender_probes, enemy_near_bases, num_enemy)
        self._execute_worker_defenders(defender_probes)

    def _assign_worker_defenders(
        self, defender_probes: Units, enemy_near_bases: dict[int, set[int]]
    ) -> int:
        bunkers = [
            b
            for b in self.manager_mediator.get_own_structures_dict[UnitID.BUNKER]
            if b.is_ready
        ]
        if len(bunkers) > 0:
            return 0

        if [
            u
            for u in self.deimos_mediator.get_enemy_proxies
            if u.type_id in {UnitID.BUNKER, UnitID.PHOTONCANNON} and u.is_ready
        ]:
            return 0

        num_probes_required: int = 0
        for s in self.deimos_mediator.get_enemy_proxies:
            if s.type_id in self._proxy_to_workers_required:
                if (
                    len(self.manager_mediator.get_enemy_army_dict[UnitID.MARAUDER])
                    >= 2
                ):
                    return 0
                num_probes_required += self._proxy_to_workers_required[s.type_id]

        num_enemy: int = 0
        if num_probes_required == 0:
            for base_tag, enemy_tags in enemy_near_bases.items():
                # look for enemy units we are interested in
                enemy_interested_in: list[Unit] = [
                    u
                    for u in self.ai.all_enemy_units
                    if u.tag in enemy_tags
                    and u.type_id in self._enemy_to_workers_required
                ]
                num_enemy += len(enemy_interested_in)
                for enemy in enemy_interested_in:
                    if enemy.type_id == UnitID.MARINE:
                        num_probes_required = 0
                        break
                    num_probes_required += self._enemy_to_workers_required[
                        enemy.type_id
                    ]

        if num_probes_required <= 1:
            return 0

        if num_probes_required > 0:
            if self.manager_mediator.get_enemy_worker_rushed:
                num_probes_required = len(self.ai.workers)
            else:
                num_probes_required = min(num_probes_required, 16)
                num_probes_required -= len(defender_probes)

            for _ in range(num_probes_required):
                if probe := self.manager_mediator.select_worker(
                    target_position=self.ai.start_location,
                    min_shield_perc=1.0,
                ):
                    self.manager_mediator.assign_role(
                        tag=probe.tag, role=UnitRole.BASE_DEFENDER
                    )

        return num_enemy

    def _unassign_worker_defenders(
        self,
        defender_probes: Units,
        enemy_near_bases: dict[int, set[int]],
        num_near_enemy: int,
    ) -> None:
        if not defender_probes:
            return

        proxies: list[Unit] = [
            p
            for p in self.deimos_mediator.get_enemy_proxies
            if p.type_id in self._proxy_to_workers_required.keys()
        ]

        # if in worker fight, then keep on the aggression
        near_enemy_workers: Units = self.manager_mediator.get_units_in_range(
            start_points=[cy_center(defender_probes)],
            distances=[15],
            query_tree=UnitTreeQueryType.EnemyGround,
        )[0].filter(lambda u: u.type_id in WORKER_TYPES)

        for probe in defender_probes:
            if (
                (not enemy_near_bases and not proxies and len(near_enemy_workers) < 6)
                or cy_distance_to_squared(probe.position, self.ai.start_location)
                > 2400.0
                or self.ai.supply_army >= 12
            ):
                self.manager_mediator.assign_role(
                    tag=probe.tag, role=UnitRole.GATHERING
                )

    def _execute_worker_defenders(self, defender_probes: Units) -> None:
        self.worker_defenders_behavior.execute(defender_probes)
