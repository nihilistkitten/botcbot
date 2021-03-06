"""Contains the ScriptManagement cog, for commands related to script management."""

from discord.ext import commands

from lib import checks
from lib.bot import BOTCBot
from lib.logic.converters import to_character, to_character_list, to_script
from lib.logic.Script import Script, script_list
from lib.preferences import load_preferences
from lib.typings.context import Context
from lib.utils import get_input, safe_send


class ScriptManagement(commands.Cog, name="Scripts"):  # type: ignore
    """Commands for script management."""

    # TODO: tools for script modification

    def __init__(self, bot: BOTCBot):
        self.bot = bot

    @commands.group()
    @checks.is_dm()
    async def script(self, ctx: Context):
        """Manage custom scripts.

        To use subcommands, use script followed by the subcommand.
        """
        if ctx.invoked_subcommand is None:
            await safe_send(ctx, "Invalid script command. Hopefully this helps:")
            return await ctx.send_help(ctx.bot.get_command("script"))

    @script.command()
    async def new(self, ctx: Context, mode: str = "json", *, name: str):
        """Create a new custom script.

        mode: How to input characters on the script.
            json: Enter the script's json file from the script creator.
            text: Enter the characters in a line break-separated message.
        name: The script's name.
        """
        with ctx.typing():

            for script in script_list(ctx, playtest=True):

                # Check duplicate names
                if script.name.lower() == name.lower():
                    _check_permission_to_edit(ctx, ctx.author.id, script)
                    # _check_permission_to_edit raises a BadArgument exception on
                    # failure, which is handled in Events.on_command_error, so we don't
                    # have to do any handling of that case here
                    await safe_send(
                        ctx,
                        (
                            f"There is already a script named {name}. "
                            "You can modify it or delete it:"
                        ),
                    )
                    await ctx.send_help(ctx.bot.get_command("script"))
                    return

        # Get the characters
        if mode == "json":
            raw_characters = (
                await get_input(
                    ctx,
                    (
                        "What characters are on the script? "
                        "Send the text of the json from the script creator."
                    ),
                )
            )[8:-3].split('"},{"id":"')
        elif mode == "text":
            raw_characters = (
                await get_input(
                    ctx,
                    (
                        "What characters are on the script? "
                        "Separate characters by line breaks."
                    ),
                )
            ).split("\n")
        else:
            raise commands.BadArgument(f"{mode} is not an accepted mode.")

        # Character list
        with ctx.typing():

            character_list = to_character_list(ctx, raw_characters)
            playtest = False
            for char_class in character_list:
                if char_class.playtest:
                    playtest = True
                    break

            # Make the script
            script = Script(
                name,
                character_list,
                editors=[ctx.message.author.id],
                playtest=playtest,
            )

        # First night _order
        raw_first_night = (
            await get_input(
                ctx,
                "What is the first night _order? Separate characters by line breaks.",
            )
        ).split("\n")

        with ctx.typing():

            first_night_list = []  # Here we're ok with duplicates
            for char in raw_first_night:
                char_class = to_character(ctx, char, script)
                first_night_list.append(char_class)

            script.first_night = first_night_list

        # Other nights _order
        raw_other_nights = (
            await get_input(
                ctx,
                (
                    "What is the _order for other nights?"
                    "Separate characters by line breaks."
                ),
            )
        ).split("\n")

        with ctx.typing():

            other_nights_list = []  # Here we're ok with duplicates
            for char in raw_other_nights:
                char_class = to_character(ctx, char, script)
                other_nights_list.append(char_class)

            script.other_nights = other_nights_list

        # save
        script.save()

        # Wrap up
        await safe_send(
            ctx,
            "Successfully created a new {playtest}script called {name}:".format(
                playtest=["", "playtest "][playtest], name=script.name
            ),
        )
        for x in script.info(ctx):
            await safe_send(ctx, x)
        return

    @script.command()
    async def info(self, ctx: Context, *, script: str):
        """View relevant info about a script, as you'd see at the start of a game."""
        script_actual = to_script(ctx, script)
        for x in script_actual.info(ctx):
            await safe_send(ctx, x)
        return

    @script.command()
    async def list(self, ctx: Context):
        """List the scripts available from this bot."""
        with ctx.typing():
            for script in script_list(
                ctx,
                playtest=(
                    ctx.bot.playtest_role is not None
                    and ctx.author in ctx.bot.playtest_role.members
                ),
            ):
                await safe_send(ctx, script.short_info(ctx))


def _check_permission_to_edit(ctx: Context, idn: int, script: Script):
    """Determine if the user represented by the ID can edit the script."""
    if idn in script.editors:
        return True

    else:
        message_text = f"You do not have permission to edit {script.name}. "
        if script.editors != []:
            editors, plural = script.editor_names(ctx)
            s = "s" if plural else ""
            verb = "are" if plural else "is"

            if plural:
                pronoun = "them"
            else:
                pronoun = load_preferences(
                    ctx.bot.get_user(script.editors[0])
                ).pronouns[1]

            message_text += (
                f"Its editor{s} {verb} {editors}. Contact {pronoun} for more info."
            )

        else:
            message_text += (
                "If you believe the script is incorrect, "
                "please contact nihilistkitten#6937 or an admin."
            )

        raise commands.BadArgument(message_text)


def setup(bot: BOTCBot):
    """Set the cog up."""
    bot.add_cog(ScriptManagement(bot))
