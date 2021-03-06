"""Contains the FortuneTeller class."""

from typing import TYPE_CHECKING

from lib.logic.Character import Townsfolk
from lib.logic.charcreation import (MorningTargetCallMixin, if_functioning,
                                    select_target)
from lib.logic.Effect import Effect

if TYPE_CHECKING:
    from lib.typings.context import GameContext


class _RedHerring(Effect):
    _name = "Red Herring"


class FortuneTeller(Townsfolk, MorningTargetCallMixin):
    """The Fortune Teller."""

    name: str = "Fortune Teller"
    playtest: bool = False
    _TARGETS = 2

    @if_functioning(True)
    async def morning(
        self, ctx: "GameContext", enabled: bool = True, epithet_string=""
    ):
        """Determine the red herring on the first night."""
        if ctx.bot.game.day_number == 0:
            target = await select_target(ctx, "Who is the red herring?")
            target.add_effect(ctx.bot.game, _RedHerring, self.parent)
        return [], []
