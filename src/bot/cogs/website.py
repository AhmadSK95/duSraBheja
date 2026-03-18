"""Website Cog — Discord commands for site modifications."""

import logging

from discord.ext import commands

from src.database import async_session
from src.services.website import execute_website_change, get_site_git_state

log = logging.getLogger("brain-bot.website")


class WebsiteCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="site")
    async def site_command(self, ctx: commands.Context, *, instruction: str):
        """Modify the website. Usage: !site <what to change>"""
        await ctx.send("Working on it...")
        try:
            async with async_session() as session:
                result = await execute_website_change(session, instruction)
            summary = result.get("summary", "Done.")
            tier = result.get("tier", "content")
            msg = f"**Done** ({tier} tier): {summary}"
            if result.get("files_modified"):
                msg += f"\nFiles modified: {', '.join(result['files_modified'])}"
            await ctx.send(msg[:2000])
        except Exception as e:
            log.exception("Website command failed")
            await ctx.send(f"Failed: {e}")

    @commands.command(name="site-status")
    async def site_status_command(self, ctx: commands.Context):
        """Check website deployment status."""
        state = get_site_git_state()
        msg = (
            f"**Branch:** {state.get('current_branch', 'unknown')}\n"
            f"**Last commit:** {state.get('last_commit', 'unknown')}\n"
            f"**Changes:** {state.get('uncommitted_changes') or 'clean'}"
        )
        await ctx.send(msg)


async def setup(bot: commands.Bot):
    await bot.add_cog(WebsiteCog(bot))
