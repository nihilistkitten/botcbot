"""Contains the Day class."""

from typing import TYPE_CHECKING, List, Optional, Tuple

from discord.ext import commands
from numpy import ceil

from lib.exceptions import AlreadyNomniatedError
from lib.logic.Player import Player
from lib.logic.playerconverter import to_player
from lib.logic.tools import generate_message_tally
from lib.logic.Vote import Vote
from lib.utils import safe_bug_report, safe_send

if TYPE_CHECKING:
    from lib.logic.Game import Game
    from lib.typings.context import DayContext


class Day:
    """Stores information about a specific day.

    Attributes
    ----------
    is_pms : bool
        Whether PMs are open.
    is_noms : bool
        Whether nominations are open.
    past_votes : List[Vote]
        The day's previous votes.
    current_vote : Vote
        The day's current vote, or None.
    about_to_die : Tuple[Player, int, int]
        The player currently about to die, their vote tally, and the announcement ID.
    vote_end_messages : List[int]
        The IDs of messages announcing the end of votes.
    """

    def __init__(self):
        self.is_pms = True  # type: bool
        self.is_noms = False  # type: bool
        self.past_votes = []  # type: List[Vote]
        self.current_vote = None  # type: Optional[Vote]
        self.about_to_die = None  # type: Optional[Tuple[Player, int, int]]
        self.vote_end_messages = []  # type: List[int]

    async def nominate(self, ctx: "DayContext", nominee_str: str, nominator: Player):
        """Begin a vote on the nominee.

        Parameters
        ----------
        ctx : DayContext
            The invocation context.
        nominee_str : str
            The user input to attempt to generate a nominee from.
        nominator : Player
            The player to be nominated.
        """
        # determine the nominee
        nominee = await _determine_nominee(ctx, nominee_str)

        # verify that the nominator can nominate
        _check_valid_nominator(ctx.bot.game, nominator, nominee)

        # verify that the nominee can be nominated
        _check_valid_nominee(ctx.bot.game, nominator, nominee)

        # check effects
        proceed = True
        for player in ctx.bot.game.seating_order:
            proceed = (
                await player.character.nomination(ctx, nominee, nominator) and proceed
            )
            effect_list = [x for x in player.effects]
            for effect in effect_list:
                proceed = await effect.nomination(ctx, nominee, nominator) and proceed

        if not proceed:
            return

        # adjust the nominator and nominee
        if not (
            nominee.is_status(ctx.bot.game, "storyteller")
            or nominee.is_status(ctx.bot.game, "traveler")
        ):
            await nominator.add_nomination(ctx)
        nominee.has_been_nominated = True

        # close pms and nominations
        await self.close_pms(ctx)
        await self.close_noms(ctx)

        # start the vote
        self.current_vote = Vote(ctx.bot.game, nominee, nominator)

        # send announcement message
        message_text = generate_nomination_message_text(
            ctx,
            nominator,
            nominee,
            traveler=self.current_vote.traveler,
            proceed=True,
            majority=int(ceil(self.current_vote.majority)),
            about_to_die=self.about_to_die,
        )
        msg = await safe_send(ctx.bot.channel, message_text, pin=True)

        # pin
        self.current_vote.announcements.append(msg.id)

        # message tally
        await self._send_message_tally(ctx)

        # start voting!
        await self.current_vote.call_next(ctx)

    async def _send_message_tally(self, ctx):
        try:
            time = (
                await ctx.bot.channel.fetch_message(self.vote_end_messages[-1])
            ).created_at
            await safe_send(
                ctx.bot.channel,
                generate_message_tally(ctx, lambda x: x["time"] >= time),
            )
        except IndexError:
            await safe_send(
                ctx.bot.channel,
                generate_message_tally(
                    ctx, lambda x: x["day"] == ctx.bot.game.day_number
                ),
            )

    async def open_pms(self, ctx: "DayContext"):
        """Open PMs."""
        self.is_pms = True
        for st in ctx.bot.game.storytellers:
            await safe_send(st.member, "PMs are now open.")
        await ctx.bot.update_status()

    async def open_noms(self, ctx: "DayContext"):
        """Open nominations."""
        self.is_noms = True
        for st in ctx.bot.game.storytellers:
            await safe_send(st.member, "Nominations are now open.")
        await ctx.bot.update_status()

    async def close_pms(self, ctx: "DayContext"):
        """Close PMs."""
        self.is_pms = False
        for st in ctx.bot.game.storytellers:
            await safe_send(st.member, "PMs are now closed.")
        await ctx.bot.update_status()

    async def close_noms(self, ctx: "DayContext"):
        """Close nominations."""
        self.is_noms = False
        for st in ctx.bot.game.storytellers:
            await safe_send(st.member, "Nominations are now closed.")
        await ctx.bot.update_status()

    async def end(self, ctx: "DayContext"):
        """End the day."""
        # cleanup effects
        for player in ctx.bot.game.seating_order:
            effect_list = [x for x in player.effects]
            for effect in effect_list:
                effect.evening_cleanup(ctx.bot.game)

        # remove the current vote
        if self.current_vote:
            await self.current_vote.cancel(ctx)

        # announcement
        await safe_send(
            ctx.bot.channel, f"{ctx.bot.player_role.mention}, go to sleep!",
        )

        # message tally
        await self._send_message_tally(ctx)

        # remove the day
        ctx.bot.game.past_days.append(self)
        ctx.bot.game.current_day = None

        # complete
        if safe_bug_report(ctx):
            await safe_send(ctx, "Successfully ended the day.")

        # new night
        await ctx.bot.game.start_night(ctx)


def _check_valid_nominee(game: "Game", nominator: Player, nominee: Player):
    """Check that the nominee is a valid nominee, else raise an exception."""
    if nominee.is_status(game, "storyteller"):  # atheist nominations
        for st in game.storytellers:
            if not st.can_be_nominated(game, nominator):
                raise commands.BadArgument(
                    "The storytellers cannot be nominated today."
                )
    elif not nominee.can_be_nominated(game, nominator):
        raise commands.BadArgument(f"{nominee.nick} cannot be nominated today.")


def _check_valid_nominator(game: "Game", nominator: Player, nominee: Player):
    """Check that nominator is a valid nominator, else raise an exception."""
    if not (
        nominator.can_nominate(game)
        or nominee.is_status(game, "traveler")
        or nominator.is_status(game, "storyteller")
    ):
        raise AlreadyNomniatedError


async def _determine_nominee(ctx: "DayContext", nominee_str: str) -> Player:
    """Determine the nominee from the string."""
    if "storyteller" in nominee_str and ctx.bot.game.script.has_atheist:  #
        # atheist nominations
        nominee = ctx.bot.game.storytellers[0]
    else:
        nominee = await to_player(
            ctx,
            nominee_str,
            only_one=True,
            includes_storytellers=ctx.bot.game.script.has_atheist,
        )
    return nominee


def generate_nomination_message_text(
    ctx: "DayContext",
    nominator: "Player",
    nominee: "Player",
    traveler=False,
    proceed=True,
    majority=0,
    about_to_die: Optional[Tuple[Player, int, int]] = None,
) -> str:
    """Generate the nomination announcement message.

    Parameters
    ----------
    ctx : DayContext
        The invocation context.
    nominator: Player
        The nominator.
    nominee: Player
        The nominee.
    traveler: bool
        Whether the nominee is a traveler.
    proceed: bool
        Whether the nomination is going to proceed.
    majority: int
        The majority to announce for the vote.
    about_to_die: Optional[Tuple[Player, int, int]]
        The player currently slated to die.

    Returns
    -------
    str
        The message announcing the nomination.
    """
    nominator_mention = (
        "the storytellers"
        if nominator.is_status(ctx.bot.game, "storyteller")
        else nominator.member.mention
    )
    nominee_mention = (
        "the storytellers"
        if nominee.is_status(ctx.bot.game, "storyteller")
        else nominee.member.mention
    )
    if traveler:  # traveler nominations
        verb = "have" if nominator.is_status(ctx.bot.game, "storyteller") else "has"
        message_text = (
            f"{ctx.bot.player_role.mention}, {nominator_mention} {verb} called for"
            f" {nominee_mention}'s exile."
        )

    else:
        verb = "have" if nominee.is_status(ctx.bot.game, "storyteller") else "has"
        message_text = (
            f"{ctx.bot.player_role.mention}, {nominee_mention} {verb} been nominated"
            f" by {nominator_mention}."
        )

    if proceed:

        message_text += f" {majority} to "

        if traveler:
            message_text += "exile."
        else:
            message_text += "execute."
            if about_to_die is not None:
                message_text += f" {about_to_die[1]} to tie."
    return message_text
