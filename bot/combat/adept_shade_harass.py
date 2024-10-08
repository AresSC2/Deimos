from dataclasses import dataclass
from typing import TYPE_CHECKING, Union

import numpy as np
from ares import ManagerMediator
from ares.managers.squad_manager import UnitSquad
from sc2.ids.ability_id import AbilityId
from sc2.position import Point2
from sc2.unit import Unit
from sc2.units import Units
from src.ares.consts import WORKER_TYPES, UnitTreeQueryType

from bot.combat.base_combat import BaseCombat
from bot.consts import COMMON_UNIT_IGNORE_TYPES
from cython_extensions import cy_attack_ready, cy_center

if TYPE_CHECKING:
    from ares import AresBot


@dataclass
class AdeptShadeHarass(BaseCombat):
    """Execute behavior adept shade squad.

    Called from `AdeptHarassManager`

    Parameters
    ----------
    ai : AresBot
        Bot object that will be running the game
    config : Dict[Any, Any]
        Dictionary with the data from the configuration file
    mediator : ManagerMediator
        Used for getting information from managers in Ares.
    """

    ai: "AresBot"
    config: dict
    mediator: ManagerMediator

    def execute(self, units: Units, **kwargs) -> None:
        """Actually execute shade squad micro.

        Parameters
        ----------
        units : UnitSquad
            The squad we want to control.
        **kwargs :
            See below.

        Keyword Arguments
        -----------------
        grid : np.ndarray
        target_dict : Dict
        cancel_shades_dict: Dict
        """

        cancel_shades_dict: dict[int, bool] = kwargs["cancel_shades_dict"]
        grid: np.ndarray = kwargs["grid"]
        target_dict: dict[int, Point2] = kwargs["target_dict"]
        # near_enemy: dict[int, Units] = self.mediator.get_units_in_range(
        #     start_points=units,
        #     distances=15,
        #     query_tree=UnitTreeQueryType.EnemyGround,
        #     return_as_dict=True,
        # )

        for unit in units:
            unit_tag: int = unit.tag
            # all_close: list[Unit] = [
            #     u
            #     for u in near_enemy[unit_tag]
            #     if not u.is_memory and u.type_id not in COMMON_UNIT_IGNORE_TYPES
            # ]
            # workers: list[Unit] = [u for u in all_close if u.type_id in WORKER_TYPES]
            target = self.ai.enemy_start_locations[0]
            if unit_tag in target_dict:
                target = target_dict[unit_tag]
            target = self.mediator.find_closest_safe_spot(from_pos=target, grid=grid)
            if (
                AbilityId.CANCEL_ADEPTSHADEPHASESHIFT in unit.abilities
                and unit_tag in cancel_shades_dict
                and cancel_shades_dict[unit_tag]
            ):
                unit(AbilityId.CANCEL_ADEPTSHADEPHASESHIFT)
            elif not unit.is_moving:
                unit.move(target)

    def _pick_target(self, units: list[Unit], targets: list[Unit]) -> Union[Unit, None]:
        """If all close targets have same health, pick the closest one.
        Otherwise, pick enemy with the lowest health.

        Parameters
        ----------
        units :
            The units we are choosing a target for.
        targets : list[Unit]
            The targets the adepts can choose from.

        Returns
        -------
        Union[Unit, None] :
            Optional thing to shoot at.

        """
        if not targets:
            return

        potential_targets: list[Unit] = []
        for unit in targets:
            if all([cy_attack_ready(self.ai, u, unit) for u in units]):
                pass
