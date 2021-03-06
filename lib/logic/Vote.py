"""Contains the Vote class."""

from typing import TYPE_CHECKING, Dict, List

from lib.preferences import load_preferences
from lib.utils import get_bool_input, list_to_plural_string, safe_send

if TYPE_CHECKING:
    from discord import Message
    from lib.logic.Player import Player
    from lib.logic.Game import Game
    from lib.typings.context import VoteContext


class Vote:
    """Stores information about a specific vote.

    Parameters
    ----------
    game : Game
        The current game.
    nominee : Player
        The vote's nominee.
    nominator : Player
        The vote's nominator.

    Attributes
    ----------
    traveler : bool
        Whether the vote is on a Traveler.
    storyteller : bool
        Whether the vote is on a Storyteller.
    announcements : List[int]
        List of IDs of announcement messages.
    prevotes : Dict[Player, int]
        IDs of players who have prevoted, and their prevotes.
    position : int
        The current position in the _order.
    votes : int
        The current number of votes.
    voted : List[Player]
        The players who have voted yes.
    order : List[Player]
        The _order of the vote.
    majority : float
        The threshold of votes required for a majority.
    nominee
    nominator
    """

    voted: List["Player"]
    announcements: List[int]
    prevotes: Dict["Player", int]
    order: List["Player"]

    def __init__(self, game: "Game", nominee: "Player", nominator: "Player"):

        self.nominee = nominee
        self.nominator = nominator
        self.traveler = nominee.is_status(game, "traveler")
        self.storyteller = nominee.is_status(game, "storyteller")
        self.announcements = []
        self.prevotes = {}
        self.position = 0
        self.votes = 0
        self.voted = []

        assert game.current_day  # mypy proofing

        # determine the _order
        if self.storyteller:
            self.order = game.seating_order
        else:
            self.order = (
                # flake8: noqa
                # this is a false positive; the space is black-enforced
                game.seating_order[game.seating_order.index(self.nominee) + 1 :]
                + game.seating_order[: game.seating_order.index(self.nominee) + 1]
            )

        # determine the majority
        if self.traveler:
            self.majority = float(len(self.order) / 2)
        else:
            self.majority = float(
                len(
                    [
                        player
                        for player in self.order
                        if not player.ghost(game, registers=True)
                    ]
                )
                / 2
            )
            if game.current_day.about_to_die:
                self.majority = max(
                    self.majority, float(game.current_day.about_to_die[1] + 1)
                )

        # check if anyone can vote twice
        for player in self.order:
            if player.is_status(game, "can_vote_twice"):
                self.order.insert(self.order.index(player), player)

    @property
    def to_vote(self):
        """Determine the next player to vote."""
        return self.order[self.position]

    async def vote(self, ctx: "VoteContext", voter: "Player", vt: int):
        """Implement a vote.

        Parameters
        ----------
        ctx : VoteContext
            The invocation context.
        voter : "Player"
            The player who is voting.
        vt : int
            1 if they vote yes, 0 otherwise.
        """
        if not voter == self.to_vote:
            # Generally caught on the command level, so no handling here
            return

        # Increment the posiiton.
        self.position += 1

        # the actual vote
        if vt:

            # change the vote count
            self.votes += voter.vote_value(ctx.bot.game, self.traveler)

            # dead vote
            if (
                not self.traveler
                and voter.ghost(ctx.bot.game, registers=True)
                and not voter.is_status(ctx.bot.game, "can_dead_vote_without_token")
            ):
                voter.dead_votes -= 1

            # tracking
            self.voted.append(voter)

        # announcement
        msg = await safe_send(
            ctx.bot.channel,
            "{voter} votes {vote}. {votes} votes.".format(
                voter=voter.nick, vote=["no", "yes"][vt], votes=self.votes
            ),
            pin=True,
        )
        self.announcements.append(msg.id)

        # call next
        if self.position != len(self.order):
            await self.call_next(ctx)
        else:
            await self.end(ctx)

    async def call_next(self, ctx: "VoteContext"):
        """Call the next voter."""
        # check dead votes
        if not self.to_vote.can_vote(ctx.bot.game, self.traveler):
            await self.vote(ctx, self.to_vote, 0)
            return await safe_send(
                self.to_vote.member, "You have no dead votes. Voting no."
            )

        # check prevote
        if self.to_vote in self.prevotes:
            return await self.vote(ctx, self.to_vote, self.prevotes[self.to_vote])

        # announcement
        await safe_send(
            ctx.bot.channel,
            f"{self.to_vote.member.mention}, your vote on {self.nominee.nick}.",
        )

        # TODO: emergency vote processing
        # subject to decision about what emergency processing looks like
        return

    async def prevote(self, ctx, voter: "Player", vt: int):
        """Implement a prevote."""
        if voter == self.to_vote:
            if await get_bool_input(
                ctx, "It is currently your vote. Would you like to vote now?"
            ):
                await self.vote(ctx, voter, vt)
                return
            else:
                await safe_send(ctx, "Vote cancelled.")
                return

        if self.order.index(voter) < self.position:
            await safe_send(ctx, "You have already voted.")
            return

        self.prevotes[voter] = vt
        await safe_send(ctx, "Successfully prevoted.")

    async def end(self, ctx: "VoteContext"):
        """End the vote."""
        # TODO: refactor probably
        if ctx.bot.game.current_day.current_vote == self:

            # end the vote
            ctx.bot.game.current_day.past_votes.append(self)
            ctx.bot.game.current_day.current_vote = None

            # announcement
            end_msg, result = await self._send_vote_end_message(ctx)

            # exile travelers
            if self.traveler and result:
                await self.nominee.character.exile(ctx)

            # open PMs and Nominations
            await ctx.bot.game.current_day.open_pms(ctx)
            await ctx.bot.game.current_day.open_noms(ctx)

            # cleanup for non-traveler nominations
            if not self.traveler:

                # edit the old message
                await self._update_old_vote_end_message(ctx, result)

                # change about_to_die
                if result:
                    ctx.bot.game.current_day.about_to_die = (
                        self.nominee,
                        self.votes,
                        end_msg.id,
                    )

            # cleanup pins
            for msg in self.announcements:
                msg_actual: Message = await ctx.bot.channel.fetch_message(msg)
                if msg_actual.pinned:
                    await msg_actual.unpin()

    async def _update_old_vote_end_message(self, ctx: "VoteContext", result: bool):
        """Update the old vote end message as appropriate."""
        if ctx.bot.game.current_day.about_to_die:
            if result or self.votes == ctx.bot.game.current_day.about_to_die[1]:
                msg = await ctx.bot.channel.fetch_message(
                    ctx.bot.game.current_day.about_to_die[2]
                )
                await msg.edit(content=msg.content[:-22] + " not" + msg.content[-22:])

                # remove about_to_die
                if not result:
                    ctx.bot.game.current_day.about_to_die = None

    async def _send_vote_end_message(self, ctx: "VoteContext"):
        """Send a message ending the vote."""
        message_text, result = self._generate_vote_end_message()
        end_msg = await safe_send(ctx.bot.channel, message_text, pin=True)
        ctx.bot.game.current_day.vote_end_messages.append(end_msg.id)
        return end_msg, result

    def _generate_vote_end_message(self):
        """Generate the vote end message."""
        result = self.votes >= self.majority
        result_type = ["executed", "exiled"][self.traveler]
        pronouns = load_preferences(self.nominee).pronouns
        message_text = (
            "{votes} votes on {nominee_nick} (nominated by {nominator_nick}). "
            "{pronoun_string}{nt} about to be {result_type}."
        )
        message_text = message_text.format(
            nt=[" not", ""][result],
            votes=self.votes,
            nominee_nick=self.nominee.nick,
            nominator_nick=self.nominator.nick,
            result_type=result_type,
            pronoun_string=pronouns[0].capitalize() + [" is", " are"][pronouns[5]],
        )
        for voter in self.voted:
            message_text += f"\n- {voter.nick}"
        return message_text, result

    async def cancel(self, ctx: "VoteContext"):
        """Cancel the vote."""
        if ctx.bot.game.current_day.current_vote == self:

            # Delete the vote
            ctx.bot.game.current_day.current_vote = None

            # Announcement
            await safe_send(ctx.bot.channel, "Nomination cancelled.")

            # Open PMs and Nominations
            await ctx.bot.game.current_day.open_pms(ctx)
            await ctx.bot.game.current_day.open_noms(ctx)

            # Cleanup character data
            if not self.traveler or self.storyteller:
                self.nominator.nominations_today -= 1
            self.nominee.has_been_nominated = False

            # Cleanup pins
            for idn in self.announcements:
                msg = await ctx.bot.channel.fetch_message(idn)
                if msg.pinned:
                    await msg.unpin()

        return
