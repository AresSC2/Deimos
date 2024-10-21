from itertools import cycle
from typing import TYPE_CHECKING, Optional

from ares import ManagerMediator
from ares.cache import property_cache_once_per_frame
from ares.consts import (
    ALL_STRUCTURES,
    LOSS_EMPHATIC_OR_WORSE,
    LOSS_OVERWHELMING_OR_WORSE,
    TOWNHALL_TYPES,
    VICTORY_MARGINAL_OR_BETTER,
    WORKER_TYPES,
    EngagementResult,
    UnitRole,
    UnitTreeQueryType,
)
from ares.managers.manager import Manager
from ares.managers.squad_manager import UnitSquad
from cython_extensions.units_utils import (
    cy_center,
    cy_closest_to,
    cy_find_units_center_mass,
)
from loguru import logger
from sc2.data import Race
from sc2.ids.unit_typeid import UnitTypeId as UnitID
from sc2.position import Point2
from sc2.units import Units

from bot.combat.base_combat import BaseCombat
from bot.combat.observer_base_defence import ObserverBaseDefence
from bot.combat.squad_combat import SquadCombat
from bot.consts import (
    CLOAK_UNIT_TYPES,
    COMMON_UNIT_IGNORE_TYPES,
    STATIC_DEFENCE,
    STEAL_FROM_ROLES,
)
from bot.managers.deimos_mediator import DeimosMediator
from cython_extensions import cy_distance_to_squared

if TYPE_CHECKING:
    from ares import AresBot


class CombatManager(Manager):
    deimos_mediator: DeimosMediator

    ATTACK_TARGET_IGNORE: set[UnitID] = {
        UnitID.CREEPTUMOR,
        UnitID.CREEPTUMORQUEEN,
        UnitID.CREEPTUMORBURROWED,
        UnitID.NYDUSCANAL,
    }
    SQUAD_ENGAGE_THRESHOLD: set[EngagementResult] = VICTORY_MARGINAL_OR_BETTER
    SQUAD_DISENGAGE_THRESHOLD: set[EngagementResult] = LOSS_OVERWHELMING_OR_WORSE
    defensive_voidrays: BaseCombat

    def __init__(
        self,
        ai: "AresBot",
        config: dict,
        mediator: ManagerMediator,
    ) -> None:
        """Handle all Reaper harass.

        This manager should assign Reapers to harass and call
        relevant combat classes to execute the harass.

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
        self.expansions_generator = None
        self.current_base_target: Point2 = self.ai.enemy_start_locations[0]
        self.aggressive: bool = False
        self._squad_id_to_engage_tracker: dict[str, bool] = dict()
        self._squad_to_target: dict[str, Point2] = dict()

        self.ground_squad_combat: BaseCombat = SquadCombat(ai, config, mediator)
        self.observer_base_defence: BaseCombat = ObserverBaseDefence(
            ai, config, mediator
        )

    @property_cache_once_per_frame
    def rally_point(self) -> Point2:
        return self.ai.main_base_ramp.top_center

    @property_cache_once_per_frame
    def attack_target(self) -> Point2:
        """Quick attack target implementation, improve this later."""
        if (
            self.ai.build_order_runner.chosen_opening == "OneBaseTempests"
            and self.ai.time < 480.0
            and (air := self.ai.enemy_units({UnitID.VOIDRAY, UnitID.TEMPEST}))
        ):
            return cy_closest_to(self.ai.start_location, air).position

        if (
            (
                self.deimos_mediator.get_enemy_rushed
                and self.ai.supply_army < 20
                and not self.manager_mediator.get_enemy_worker_rushed
            )
            or (
                self.ai.build_order_runner.chosen_opening == "OneBaseTempests"
                and self.ai.race not in {Race.Terran, Race.Zerg}
                and len(self.manager_mediator.get_own_army_dict[UnitID.TEMPEST]) <= 2
            )
            or (
                len(self.manager_mediator.get_enemy_army_dict[UnitID.MARINE]) > 6
                and self.ai.supply_army < 16
            )
            or (
                self.deimos_mediator.get_enemy_went_mass_ling
                and self.ai.supply_army < 34
            )
        ):
            return self.rally_point

        enemy_structure_pos: Optional[Point2] = None
        if enemy_structures := self.ai.enemy_structures.filter(
            lambda s: s.type_id not in self.ATTACK_TARGET_IGNORE
        ):
            enemy_structure_pos = enemy_structures.closest_to(
                self.manager_mediator.get_enemy_nat
            ).position

        if (
            self.ai.build_order_runner.chosen_opening == "OneBaseTempests"
            and self.ai.time < 360.0
            and not enemy_structure_pos
        ):
            return self.ai.enemy_start_locations[0]

        own_center_mass, num_own = cy_find_units_center_mass(
            self.manager_mediator.get_units_from_role(role=UnitRole.ATTACKING),
            10,
        )
        # idea here is if we are near enemy structures/production, don't get distracted
        if (
            enemy_structure_pos
            and cy_distance_to_squared(own_center_mass, enemy_structure_pos) < 450.0
        ):
            return enemy_structure_pos

        enemy_center_mass, num_enemy = cy_find_units_center_mass(
            [
                u
                for u in self.manager_mediator.get_enemy_ground
                if u.type_id not in WORKER_TYPES
                and u.type_id not in ALL_STRUCTURES
                and not u.is_burrowed
                and not u.is_cloaked
            ],
            10,
        )

        if self.ai.build_order_runner.chosen_opening != "OneBaseTempests":
            all_close_enemy: Units = self.manager_mediator.get_units_in_range(
                start_points=[Point2(enemy_center_mass)],
                distances=11.5,
                query_tree=UnitTreeQueryType.EnemyGround,
            )[0]
            if self.ai.get_total_supply(all_close_enemy) >= 18:
                return Point2(enemy_center_mass)

        if enemy_structure_pos:
            return enemy_structure_pos
        else:
            # cycle through base locations
            if self.ai.is_visible(self.current_base_target):
                if not self.expansions_generator:
                    base_locations: list[Point2] = [
                        i for i in self.ai.expansion_locations_list
                    ]
                    self.expansions_generator = cycle(base_locations)

                self.current_base_target = next(self.expansions_generator)

            return self.current_base_target

    @property_cache_once_per_frame
    def main_fight_result(self) -> EngagementResult:
        attackers: Units = self.manager_mediator.get_units_from_roles(
            roles=STEAL_FROM_ROLES
        )
        army_mass: tuple[float, float] = cy_find_units_center_mass(attackers, 12.0)[0]
        army_near_mass: Units = attackers.filter(
            lambda u: cy_distance_to_squared(u.position, army_mass) < 150.0
        )

        return self.manager_mediator.can_win_fight(
            own_units=army_near_mass,
            enemy_units=self.manager_mediator.get_cached_enemy_army
            + self.ai.enemy_structures(UnitID.PLANETARYFORTRESS),
        )

    async def update(self, iteration: int) -> None:
        self._manage_observer_base_defence()
        self._check_aggressive_status()
        # self._manage_combat_roles()

        self._manage_main_combat()
        # self._handle_defenders()

    def _manage_combat_roles(self) -> None:
        if self.aggressive:
            self.manager_mediator.switch_roles(
                from_role=UnitRole.DEFENDING, to_role=UnitRole.ATTACKING
            )
        else:
            self.manager_mediator.switch_roles(
                from_role=UnitRole.ATTACKING, to_role=UnitRole.DEFENDING
            )

    def _check_aggressive_status(self) -> None:
        reapers: Units = self.manager_mediator.get_enemy_army_dict[UnitID.REAPER]
        if self.manager_mediator.get_enemy_went_marine_rush and self.ai.supply_army < 50:
            self.aggressive = False
        elif len(reapers) >= 3 and (
            (self.ai.supply_army * 1.2) < self.ai.get_total_supply(reapers)
            or self.ai.supply_army < 14
        ):
            self.aggressive = False
        elif (
            self.ai.enemy_race in {Race.Zerg, Race.Terran}
            and len(self.manager_mediator.get_enemy_army_dict[UnitID.MUTALISK]) == 0
            and len(reapers) <= 3
            and not self.manager_mediator.get_enemy_roach_rushed
        ) or self.ai.build_order_runner.chosen_opening == "OneBaseTempests":
            self.aggressive = True

        elif self.aggressive:
            self.aggressive = self.main_fight_result not in LOSS_EMPHATIC_OR_WORSE
            if not self.aggressive:
                logger.info(f"{self.ai.time_formatted} - Turned aggression off")
        else:
            self.aggressive = self.main_fight_result in VICTORY_MARGINAL_OR_BETTER
            if self.aggressive:
                logger.info(f"{self.ai.time_formatted} - Turned aggression on")

    def _manage_main_combat(self) -> None:
        squads: list[UnitSquad] = self.manager_mediator.get_squads(
            role=UnitRole.ATTACKING, squad_radius=9.0
        )
        if len(squads) == 0:
            return

        army: Units = self.manager_mediator.get_units_from_role(role=UnitRole.ATTACKING)

        pos_of_main_squad: Point2 = self.manager_mediator.get_position_of_main_squad(
            role=UnitRole.ATTACKING
        )
        main_target: Point2 = (
            self.attack_target if self.aggressive else self.rally_point
        )
        if not self.aggressive:
            if (
                ground_threats := self.manager_mediator.get_main_ground_threats_near_townhall
            ):
                main_target = Point2(cy_center(ground_threats))
            elif (
                air_threats := self.manager_mediator.get_main_ground_threats_near_townhall
            ):
                main_target = Point2(cy_center(air_threats))

        for squad in squads:
            move_to: Point2 = (
                main_target
                if squad.main_squad or not self.aggressive
                else pos_of_main_squad
            )
            all_close_enemy: Units = self.manager_mediator.get_units_in_range(
                start_points=[squad.squad_position],
                distances=18.5,
                query_tree=UnitTreeQueryType.AllEnemy,
            )[0].filter(lambda u: u.type_id not in COMMON_UNIT_IGNORE_TYPES)

            self._track_squad_engagement(army, squad, all_close_enemy)
            can_engage: bool = self._squad_id_to_engage_tracker[squad.squad_id]
            if (
                self.deimos_mediator.get_enemy_rushed
                and self.ai.time < 330.0
                and not self.manager_mediator.get_enemy_ling_rushed
            ):
                can_engage = True

            # self._manage_squad_target(squad, can_engage, all_close_enemy, move_to)

            self.ground_squad_combat.execute(
                squad.squad_units,
                always_fight_near_enemy=not self.aggressive
                or (
                    self.ai.build_order_runner.chosen_opening == "OneBaseTempests"
                    and cy_distance_to_squared(squad.squad_position, self.attack_target)
                    > 900
                ),
                all_close_enemy=all_close_enemy,
                can_engage=can_engage,
                main_squad=squad.main_squad,
                target=move_to
                # target=self._squad_to_target[squad.squad_id],
            )

    def _manage_squad_target(
        self,
        squad: UnitSquad,
        can_engage: bool,
        all_close_enemy: Units,
        default_move_to: Point2,
    ) -> None:
        squad_id: str = squad.squad_id
        if squad_id not in self._squad_to_target:
            self._squad_to_target[squad_id] = default_move_to
            return

        if not self.aggressive:
            self._squad_to_target[squad_id] = self.rally_point
            return

        # switch between default and a calculated target if possible
        current_target: Point2 = self._squad_to_target[squad_id]
        # we only change target if close enemy and we can't engage
        if not can_engage and all_close_enemy and current_target == default_move_to:
            enemy_townhalls: Units = self.ai.enemy_structures(TOWNHALL_TYPES)
            furthest_townhall = None
            furthest_dist: float = 0.0
            for th in enemy_townhalls:
                if (
                    th.position
                    in {
                        self.ai.enemy_start_locations[0],
                        self.manager_mediator.get_enemy_nat,
                    }
                    or cy_distance_to_squared(th.position, squad.squad_position) < 312.0
                ):
                    continue

                dist = cy_distance_to_squared(th.position, squad.squad_position)
                if dist > furthest_dist:
                    furthest_dist = dist
                    furthest_townhall = th.position

            if furthest_townhall:
                self._squad_to_target[squad_id] = furthest_townhall
                return

        # change if close to alternative target and can't engage
        # or nothing at the alternative place
        if (
            current_target != default_move_to
            and not can_engage
            and cy_distance_to_squared(current_target, squad.squad_position) < 144
        ) or len(
            [
                u
                for u in self.ai.all_enemy_units
                if cy_distance_to_squared(u.position, current_target) < 256
            ]
        ) == 0:
            self._squad_to_target[squad_id] = default_move_to
            return

    def _track_squad_engagement(
        self, attackers: Units, squad: UnitSquad, close_enemy: Units
    ) -> None:
        """
        Not only do we check units in this squad, we should check all
        our units with UnitRole.Attacking, since another squad might be
        situated in a flank position etc.

        Parameters
        ----------
        close_enemy
        squad

        Returns
        -------

        """
        close_enemy: Units = close_enemy.filter(
            lambda u: (u.type_id not in ALL_STRUCTURES or u.type_id in STATIC_DEFENCE)
            and u.type_id not in COMMON_UNIT_IGNORE_TYPES
        )

        squad_id: str = squad.squad_id
        if squad_id not in self._squad_id_to_engage_tracker:
            self._squad_id_to_engage_tracker[squad_id] = False

        # if we are defending just keep this True for now
        # maybe improve later
        if (
            not self.aggressive
            or self.ai.build_order_runner.chosen_opening == "OneBaseTempests"
        ):
            self._squad_id_to_engage_tracker[squad_id] = True
            return

        # no enemy nearby, makes no sense to engage
        if not close_enemy:
            self._squad_id_to_engage_tracker[squad_id] = False
            return

        enemy_pos: Point2 = close_enemy.center

        own_attackers_nearby: Units = attackers.filter(
            lambda a: cy_distance_to_squared(a.position, enemy_pos) < 240.0
        )

        fight_result: EngagementResult = self.manager_mediator.can_win_fight(
            own_units=own_attackers_nearby, enemy_units=close_enemy
        )

        # currently engaging, see if we should disengage
        if self._squad_id_to_engage_tracker[squad.squad_id]:
            if fight_result in self.SQUAD_DISENGAGE_THRESHOLD:
                self._squad_id_to_engage_tracker[squad.squad_id] = False
        # not engaging, check if we can
        elif fight_result in self.SQUAD_ENGAGE_THRESHOLD:
            self._squad_id_to_engage_tracker[squad.squad_id] = True

    def _manage_observer_base_defence(self) -> None:
        if self.ai.time > 300.0:
            return
        base_defence_observers: Units = self.manager_mediator.get_units_from_role(
            role=UnitRole.BASE_DEFENDER, unit_type=UnitID.OBSERVER
        )

        enemy_cloak: Units = self.ai.all_enemy_units(CLOAK_UNIT_TYPES)

        # assign base defender observer if needed
        if len(enemy_cloak) > 0 and len(base_defence_observers) == 0:
            if attacking_observers := self.manager_mediator.get_units_from_role(
                role=UnitRole.ATTACKING, unit_type=UnitID.OBSERVER
            ):
                self.manager_mediator.assign_role(
                    tag=attacking_observers[0].tag, role=UnitRole.BASE_DEFENDER
                )

        if not base_defence_observers:
            return

        move_to: Point2
        if enemy_cloak:
            move_to = cy_closest_to(self.ai.start_location, enemy_cloak).position
        else:
            move_to = self.ai.game_info.map_center

        self.observer_base_defence.execute(
            base_defence_observers, enemy_cloak=enemy_cloak, move_to=move_to
        )
