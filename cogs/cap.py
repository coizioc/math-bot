"""Defines the functions used for handling citadel caps."""
from datetime import datetime
from datetime import timedelta
import json
import logging
import asyncio
import aiohttp
from discord.ext import commands
from utils.config import cap_channel
from utils.config import player_url
from utils.helpers import get_clan_list

async def check_alog(username, search_string):
    """Returns date if search string is in user history, or if it has previously been recorded."""
    url = f"{player_url}{username}&activities=20"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as alog_resp:
            data_json = await alog_resp.json()
    try:
        activities = data_json['activities']
    except KeyError:
        logging.info(f"{username}'s profile is private.")
        return None
    for activity in activities:
        if search_string in activity['details']:
            cap_date = activity['date']
            # logging.info(f"{search_string} found: {username}, {cap_date}")
            db_date = datetime.strptime(cap_date, "%d-%b-%Y %H:%M")
            return db_date
    return None

async def in_cap_channel(ctx):
    """Checks if the context channel is the cap channel."""
    return ctx.channel.id == cap_channel

async def cap_handler_and_channel(ctx):
    """Checks if the channel is the cap channel and the user is a cap handler."""
    return ("cap handler" in map(lambda r: r.name, ctx.author.roles)) and in_cap_channel(ctx)

class Cap():
    """Defines the cap command and functions."""

    def __init__(self, bot):
        self.bot = bot
        self.bot.build_tick_checker = self.bot.loop.create_task(self.get_build_tick())
        self.bot.cap_report = self.bot.loop.create_task(self.report_caps())

    @commands.group(invoke_without_command=True)
    async def cap(self, ctx):
        """Defines the cap command group."""
        if ctx.invoked_subcommand is None:
            await ctx.send("Try '$cap help'.")

    @cap.command()
    @commands.check(in_cap_channel)
    async def list(self, ctx):
        """Lists the capped users."""
        userlist = []
        async for msg_lines in ctx.channel.history(limit=500).filter(
                lambda m: m.author == self.bot.user).filter(lambda m: "capped" in m.content).map(
                    lambda m: m.content.split("\n")):
            for cap_report in msg_lines:
                name_index = cap_report.find(" has")
                userlist.append(cap_report[:name_index])
        userlist.reverse()
        ret_str = ""
        for (pos, val) in enumerate(userlist):
            ret_str += f"{pos+1}. {val}\n"
        await ctx.send(ret_str)

    @cap.command()
    async def clan(self, ctx):
        """Returns the number of people in the clan."""
        clan_list = await get_clan_list
        await ctx.send(len(clan_list))

    @cap.command()
    @commands.check(cap_handler_and_channel)
    async def force(self, ctx, *, force_user):
        """Forces a single user to update."""
        out_msg = ""
        all_stmt = f"""SELECT rsn, last_cap_time FROM caps;"""
        user_stmt = f"""SELECT rsn, last_cap_time FROM caps WHERE rsn = $1;"""
        async with self.bot.pool.acquire() as con:
            if force_user == "all":
                statement = all_stmt
            else:
                statement = user_stmt
            records = await con.fetch(statement, force_user)
            for record in records:
                rsn = record['rsn']
                last_cap = record['last_cap_time']
                if last_cap is not None:
                    last_cap = datetime.strftime(last_cap, "%d-%b-%Y %H:%M")
                    datetime_list = last_cap.split(" ")
                    out_msg += (f"{rsn} has capped at the citadel on {datetime_list[0]} "
                                f"at {datetime_list[1]}.\n")
                else:
                    out_msg += f"{rsn} not in database.\n"
        await ctx.send(out_msg)

    @cap.command(name="del")
    @commands.check(cap_handler_and_channel)
    async def _del(self, ctx, which):
        """Deletes specified message."""
        def is_bot(msg):
            """Checks if the user is the bot."""
            return msg.author == self.bot.user

        def is_bot_noncap(msg):
            """Checks if the user is the bot and the message is a cap message."""
            return is_bot(msg) and "capped" not in msg.content

        if which == "all":
            await ctx.channel.purge(limit=200, check=is_bot)
        elif which == "noncap":
            await ctx.channel.purge(limit=200, check=is_bot_noncap)
        else:
            before_msg = await ctx.channel.get_message(which)
            await ctx.channel.purge(limit=200, check=is_bot, before=before_msg)

    @cap.command(name="recheck")
    async def recheck(self, ctx, *user):
        """Rechecks all alogs for cap messages."""
        await self.report_caps(list(user))

    @commands.group(invoke_without_command=True)
    async def tick(self, ctx):
        """Displays the last build tick."""
        if ctx.invoked_subcommand is None:
            await ctx.send(f"Last build tick: {self.bot.last_build_tick}")

    class Day(commands.Converter):
        """Maps a day to an integer."""
        async def convert(self, ctx, argument):
            day_dict = {"monday":0, "tuesday":1, "wednesday":2, "thursday":3, "friday":4,
                        "saturday":5, "sunday":6}
            return day_dict.get(argument.lower(), None)

    @tick.command(name="move")
    async def _move(self, ctx, new_day: Day, new_hour):
        """Moves build tick to new time."""
        with open(f"./resources/reset.json", "r+") as reset_file:
            reset = json.load(reset_file)
        if new_day is None:
            await ctx.send("Must enter a valid day of the week (Monday, Tuesday, etc.)")
            return
        if new_hour is None:
            await ctx.send("Must enter an hour between 0 and 24.")
            return

        reset = {}
        reset["day"] = new_day
        reset["hour"] = new_hour
        with open(f"./resources/reset.json", "w") as reset_file:
            json.dump(reset, reset_file)
        await ctx.send(f"Build tick changed.")
        await self.get_build_tick()

    async def get_cap_list(self, clan_list):
        """Gets the list of all clan members who have capped."""
        cap_list = []
        for user in clan_list:
            cap_date = await check_alog(user, "capped")
            # Add the cap only if it exists, it's been since the last build tick, and
            # there's no message already in the channel.
            if cap_date is not None:
                logging.info(f"Cap date for {user}: {cap_date}")
                if cap_date < self.bot.last_build_tick:
                    logging.info("Not reporting cap: before build tick.")
                else:
                    nice_date = datetime.strftime(cap_date, "%d-%b-%Y %H:%M")
                    datetime_list = nice_date.split(" ")
                    cap_str = (f"{user} has capped at the citadel on {datetime_list[0]}"
                               f" at {datetime_list[1]}.")
                    cap_msg_list = await self.bot.cap_ch.history().filter(
                        lambda m: m.author == self.bot.user).map(lambda m: m.content).filter(
                            lambda m, c_s=cap_str: c_s in m).flatten()
                    if cap_msg_list:
                        logging.info("Not reporting cap: cap message exists.")
                    if not cap_msg_list:
                        cap_list.append((user, cap_date, cap_str))
        logging.info(cap_list)
        return cap_list

    async def report_caps(self, user=()):
        """Reports caps."""
        await self.bot.wait_until_ready()
        self.bot.cap_ch = self.bot.get_channel(cap_channel)
        while not self.bot.is_closed():
            logging.info(f"Last build tick: {self.bot.last_build_tick}")
            if not user:
                clan_list = await get_clan_list()
            else:
                clan_list = user
            # Make sure all names are in the database prior to adding new cap records
            await self.update_names(clan_list)
            cap_list = await self.get_cap_list(clan_list)

            for name, cap_date, cap_str in cap_list:
                # Send messages to channel reporting the caps
                await self.bot.cap_ch.send(cap_str)

                # Put caps in database - update records to most recent time
                async with self.bot.pool.acquire() as con:
                    async with con.transaction():
                        upsert_stmt = f"""INSERT INTO caps(rsn, last_cap_time)
                            VALUES($1, $2) ON CONFLICT ON CONSTRAINT caps_rsn_key 
                            DO UPDATE SET last_cap_time = EXCLUDED.last_cap_time;
                            """
                        await con.execute(upsert_stmt, name, cap_date)
            await asyncio.sleep(600)

    async def update_names(self, clan_list):
        """Adds all names from the clan list to the database."""
        async with self.bot.pool.acquire() as con:
            async with con.transaction():
                upsert_stmt = """INSERT INTO rs(rsn) VALUES($1) ON CONFLICT (rsn) DO NOTHING;"""
                names = [(name,) for name in clan_list]
                await con.executemany(upsert_stmt, names)

    async def get_build_tick(self):
        """Returns the most recent build tick - Wednesday 1600 UTC"""
        try:
            await self.bot.wait_until_ready()
            with open("./resources/reset.json", "r") as reset_file:
                reset = json.load(reset_file)
            reset_day = reset["day"]
            reset_hour = reset["hour"]
            self.bot.cap_ch = self.bot.get_channel(cap_channel)
            while not self.bot.is_closed():
                today_utc = datetime.utcnow()
                d_off = (today_utc.weekday() - reset_day) % 7
                h_off = (today_utc.hour - reset_hour)
                m_off = today_utc.minute
                s_off = today_utc.second
                ms_off = today_utc.microsecond
                tdel = timedelta(
                    days=d_off, hours=h_off, minutes=m_off, seconds=s_off, microseconds=ms_off)
                self.bot.last_build_tick = today_utc - tdel
                logging.info("Last build tick:")
                logging.info(self.bot.last_build_tick)

                await asyncio.sleep(3600)
        except Exception as e:
            print(e)

def setup(bot):
    """Adds the cog to the bot."""
    bot.add_cog(Cap(bot))
