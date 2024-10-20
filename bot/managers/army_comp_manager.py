from typing import TYPE_CHECKING, Any

from ares import ManagerMediator
from ares.consts import WORKER_TYPES
from ares.managers.manager import Manager
from sc2.data import Race
from sc2.ids.unit_typeid import UnitTypeId as UnitID

from bot.consts import RequestType
from bot.managers.deimos_mediator import DeimosMediator

if TYPE_CHECKING:
    from ares import AresBot


class ArmyCompManager(Manager):
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

        self.deimos_requests_dict = {
            RequestType.GET_ARMY_COMP: lambda kwargs: self._army_comp
        }

        self._army_comp: dict = self.stalker_immortal_comp

    def manager_request(
        self,
        receiver: str,
        request: RequestType,
        reason: str = None,
        **kwargs,
    ) -> Any:
        """Fetch information from this Manager so another Manager can use it.

        Parameters
        ----------
        receiver :
            This Manager.
        request :
            What kind of request is being made
        reason :
            Why the reason is being made
        kwargs :
            Additional keyword args if needed for the specific request, as determined
            by the function signature (if appropriate)

        Returns
        -------
        Optional[Union[Dict, DefaultDict, Coroutine[Any, Any, bool]]] :
            Everything that could possibly be returned from the Manager fits in there

        """
        return self.deimos_requests_dict[request](kwargs)

    @property
    def adept_only_comp(self) -> dict:
        return {
            UnitID.ADEPT: {"proportion": 1.0, "priority": 0},
        }

    @property
    def stalker_colossus_comp(self) -> dict:
        return {
            UnitID.COLOSSUS: {"proportion": 0.15, "priority": 1},
            UnitID.STALKER: {"proportion": 0.85, "priority": 2},
        }

    @property
    def stalker_immortal_comp(self) -> dict:
        return {
            UnitID.IMMORTAL: {"proportion": 0.15, "priority": 1},
            UnitID.STALKER: {"proportion": 0.85, "priority": 2},
        }

    @property
    def stalker_phoenix_comp(self) -> dict:
        return {
            UnitID.STALKER: {"proportion": 0.65, "priority": 1},
            UnitID.PHOENIX: {"proportion": 0.35, "priority": 0},
        }

    @property
    def stalker_tempests_comp(self) -> dict:
        return {
            UnitID.STALKER: {"proportion": 0.75, "priority": 1},
            UnitID.TEMPEST: {"proportion": 0.25, "priority": 0},
        }

    @property
    def stalker_comp(self) -> dict:
        return {
            UnitID.STALKER: {"proportion": 1.0, "priority": 0},
        }

    @property
    def tempests_comp(self) -> dict:
        return {
            UnitID.TEMPEST: {"proportion": 1.0, "priority": 0},
        }

    @property
    def zealot_only(self) -> dict:
        return {
            UnitID.ZEALOT: {"proportion": 1.0, "priority": 0},
        }

    @property
    def core_ready(self) -> bool:
        return (
            len(
                [
                    c
                    for c in self.manager_mediator.get_own_structures_dict[
                        UnitID.CYBERNETICSCORE
                    ]
                    if c.is_ready
                ]
            )
            > 0
        )

    async def update(self, iteration: int) -> None:
        enemy_army_dict: dict = self.manager_mediator.get_enemy_army_dict
        if (
            self.manager_mediator.get_enemy_worker_rushed and self.ai.supply_used < 26
        ) or (self.manager_mediator.get_enemy_ling_rushed and not self.core_ready):
            self._army_comp = self.zealot_only
        elif self.ai.build_order_runner.chosen_opening == "OneBaseTempests" or (
            self.ai.enemy_race == Race.Terran
            and (
                len(self.ai.enemy_structures(UnitID.BUNKER)) >= 2
                or (
                    len(enemy_army_dict[UnitID.SIEGETANKSIEGED])
                    + len(enemy_army_dict[UnitID.SIEGETANK])
                    >= 3
                )
            )
        ):
            self._army_comp = self.tempests_comp
        elif (
            len(enemy_army_dict[UnitID.MARINE]) > 6
            and self.ai.supply_army < 32
            and not self.ai.enemy_structures(UnitID.FACTORYTECHLAB)
        ):
            self._army_comp = self.stalker_comp
        elif (
            len(enemy_army_dict[UnitID.MUTALISK]) > 1
            and len(self.manager_mediator.get_own_army_dict[UnitID.PHOENIX]) < 4
        ):
            self._army_comp = self.stalker_phoenix_comp
        elif (
            self.ai.supply_used > 120
            and self.ai.enemy_race != Race.Zerg
            and len(self.manager_mediator.get_own_army_dict[UnitID.MARINE]) < 10
        ):
            self._army_comp = self.stalker_tempests_comp
        elif self.manager_mediator.get_enemy_ling_rushed and (
            self.ai.supply_army
            < self.ai.get_total_supply(enemy_army_dict[UnitID.ZERGLING])
            or self.ai.supply_army < 20
        ):
            self._army_comp = self.adept_only_comp
        else:
            supply_light: float = self.ai.get_total_supply(
                [
                    u
                    for u in self.ai.enemy_units
                    if u.is_light and u.type_id not in WORKER_TYPES and not u.is_flying
                ]
            )
            if supply_light >= 20:
                self._army_comp = self.stalker_colossus_comp
            else:
                self._army_comp = self.stalker_immortal_comp
