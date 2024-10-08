"""Handle Reaper Harass."""
from itertools import cycle
from typing import TYPE_CHECKING

from ares import ManagerMediator
from ares.consts import UnitRole, UnitTreeQueryType
from ares.managers.manager import Manager
from sc2.ids.unit_typeid import UnitTypeId as UnitID
from sc2.position import Point2
from sc2.unit import Unit
from sc2.units import Units

from bot.combat.base_combat import BaseCombat
from bot.combat.oracle_harass import OracleHarass
from bot.consts import STEAL_FROM_ROLES
from bot.managers.deimos_mediator import DeimosMediator

# from bot.combat.oracle_scout import OracleScout

if TYPE_CHECKING:
    from ares import AresBot


class OracleManager(Manager):
    deimos_mediator: DeimosMediator

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

        self._oracle_harass: BaseCombat = OracleHarass(ai, config, mediator)
        # self._oracle_scout: BaseUnit = OracleScout(ai, config, mediator)

        # TODO: make the target more sophisticated
        self.oracle_harass_target: Point2 = ai.enemy_start_locations[0]
        # key: oracle tag, value: frame number weapon is ready
        self.oracle_to_weapon_ready: dict[int, int] = {}
        # in frames, this might need tweaking
        self.ORACLE_WEAPON_COOLDOWN: int = 5

        self.expansions_generator = None
        self.current_scout_target: Point2 = self.ai.enemy_start_locations[0]

    async def update(self, iteration: int) -> None:
        # oracles get assigned harass by default, low priority task
        harass_oracles: Units = self.manager_mediator.get_units_from_role(
            role=UnitRole.HARASSING_ORACLE
        )
        if iteration % 8 == 0:
            self._assign_oracle_roles(harass_oracles)

        if self.oracle_harass_active:
            for unit in self.ai.enemy_units:
                if unit.tag in self.ai._enemy_units_previous_map:
                    previous_frame_unit: Unit = self.ai._enemy_units_previous_map[
                        unit.tag
                    ]
                    # Check if a unit took damage this frame and then trigger event
                    if (
                        unit.health < previous_frame_unit.health
                        or unit.shield < previous_frame_unit.shield
                    ):
                        self.on_unit_took_damage(unit)
        else:
            self._update_oracle_scout_target()

        self._control_oracles(harass_oracles)

    def on_unit_took_damage(self, unit: Unit) -> None:
        # check if there is only one oracle nearby, then we can update that oracle's weapon cooldown
        if not unit.is_mine:
            nearby_own_units: Units = self.manager_mediator.get_units_in_range(
                start_points=[unit.position],
                distances=12,
                query_tree=UnitTreeQueryType.AllOwn,
                return_as_dict=False,
            )[0]
            if (
                len(nearby_own_units) == 1
                and nearby_own_units[0].type_id == UnitID.ORACLE
            ):
                self.oracle_to_weapon_ready[nearby_own_units[0].tag] = (
                    self.ai.state.game_loop + self.ORACLE_WEAPON_COOLDOWN
                )

    @property
    def oracle_harass_active(self) -> bool:
        return True
        # aa_dps: float = 0.0
        # for unit in self.manager_mediator.get_cached_enemy_army:
        #     aa_dps += unit.air_dps
        # for s in self.ai.enemy_structures:
        #     aa_dps += s.air_dps
        #
        # return aa_dps < 25.0

    def _assign_oracle_roles(self, harass_oracles: Units):
        # decide if harassers should switch to scouting
        if not self.oracle_harass_active and harass_oracles:
            self.manager_mediator.batch_assign_role(
                tags=harass_oracles.tags, role=UnitRole.SCOUTING
            )
        # oracles get defending role by default, switch to harass
        else:
            if defending_oracles := self.manager_mediator.get_units_from_roles(
                roles=STEAL_FROM_ROLES, unit_type=UnitID.ORACLE
            ):
                self.manager_mediator.batch_assign_role(
                    tags=defending_oracles.tags, role=UnitRole.HARASSING_ORACLE
                )

    def _control_oracles(self, harass_oracles: Units):
        self._oracle_harass.execute(
            harass_oracles, oracle_to_weapon_ready=self.oracle_to_weapon_ready
        )

        # if scouting_oracles := self.manager_mediator.get_units_from_role(
        #     role=UnitRole.SCOUTING, unit_type=UnitID.ORACLE
        # ):
        #     self._oracle_scout.execute(
        #         scouting_oracles, scout_target=self.current_scout_target
        #     )

    def _update_oracle_scout_target(self):
        if not self.expansions_generator:
            self.expansions_generator = cycle(
                [i for i in self.ai.expansion_locations_list]
            )
            self.current_scout_target = next(self.expansions_generator)

        if self.ai.is_visible(self.current_scout_target):
            self.current_scout_target = next(self.expansions_generator)
