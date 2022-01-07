import re
import logging

from datetime import datetime, timedelta
from discord_slash import SlashContext, SlashCommandOptionType
from discord_slash.model import BaseCommandObject
from discord_slash.utils.manage_commands import create_choice, create_option
from typing import List, Tuple
from balance import Balance
from user import User
from config import CURRENCY_PRECISION


def dm_only(coro):
    async def wrapper(ctx, *args, **kwargs):
        if ctx.guild:
            await ctx.send('This command can only be used via a Private Message.')
            return
        return await coro(ctx, *args, **kwargs)

    return wrapper


def server_only(coro):
    async def wrapper(ctx: SlashContext, *args, **kwargs):
        if not ctx.guild:
            await ctx.send('This command can only be used in a server.')
            return
        return await coro(ctx, *args, **kwargs)

    return wrapper


# Thanks Stackoverflow
def de_emojify(text):
    regrex_pattern = re.compile("["
                                u"\U0001F600-\U0001F64F"  # emoticons
                                u"\U0001F300-\U0001F5FF"  # symbols & pictographs
                                u"\U0001F680-\U0001F6FF"  # transport & map symbols
                                u"\U0001F1E0-\U0001F1FF"  # flags (iOS)
                                u"\U00002500-\U00002BEF"  # chinese char
                                u"\U00002702-\U000027B0"
                                u"\U00002702-\U000027B0"
                                u"\U000024C2-\U0001F251"
                                u"\U0001f926-\U0001f937"
                                u"\U00010000-\U0010ffff"
                                u"\u2640-\u2642"
                                u"\u2600-\u2B55"
                                u"\u200d"
                                u"\u23cf"
                                u"\u23e9"
                                u"\u231a"
                                u"\ufe0f"  # dingbats
                                u"\u3030"
                                "]+", re.UNICODE)
    return regrex_pattern.sub(r'', text)


def get_user_by_id(users_by_id,
                   user_id: int,
                   guild_id: int = None,
                   exact: bool = False,
                   throw_exceptions=True) -> User:
    """
    Tries to find a matching entry for the user and guild id.
    :param exact: whether the global entry should be used if the guild isn't registered
    :return:
    The found user. It will never return None if throw_exceptions is True, since an ValueError exception will be thrown instead.
    """
    result = None

    if user_id in users_by_id:
        endpoints = users_by_id[user_id]
        if isinstance(endpoints, dict):
            result = endpoints.get(guild_id, None)
            if not result and not exact:
                result = endpoints.get(None, None)  # None entry is global
            if not result and throw_exceptions:
                raise ValueError("User {name} not registered for this guild")
        else:
            logging.error(
                f'users_by_id contains invalid entry! Associated data with {user_id=}: {result=} {endpoints=} ({guild_id=})')
            if throw_exceptions:
                raise ValueError("This is caused due to a bug in the bot. Please contact dev.")
    elif throw_exceptions:
        logging.error(f'Dont know user {user_id=}')
        raise ValueError("Unknown user {name}. Please register first.")

    return result


def add_guild_option(guilds, command: BaseCommandObject, description: str):
    command.options.append(
        create_option(
            name="guild",
            description=description,
            required=False,
            option_type=SlashCommandOptionType.STRING,
            choices=[
                create_choice(
                    name=guild.name,
                    value=str(guild.id)
                ) for guild in guilds
            ]
        )
    )


def calc_percentage(then: float, now: float) -> str:
    diff = now - then
    if diff == 0.0:
        result = '0'
    elif then > 0:
        result = f'{round(100 * (diff / then), ndigits=3)}'
    else:
        result = 'nan'
    return result


def calc_timedelta_from_time_args(time_str: str) -> timedelta:
    """
    Calculates timedelta from given time args.
    Arg Format:
      <n><f>
      where <f> can be m (minutes), h (hours), d (days) or w (weeks)

      or valid time string

    :raise:
      ValueError if invalid arg is given
    :return:
      Calculated timedelta or None if None was passed in
    """

    if not time_str:
        return None

    time_str = time_str.lower()

    # Different time formats: True or False indicates whether the date is included.
    formats = [
        (False, "%H:%M:%S"),
        (False, "%H:%M"),
        (False, "%H"),
        (True, "%d.%m.%Y %H:%M:%S"),
        (True, "%d.%m.%Y %H:%M"),
        (True, "%d.%m.%Y %H"),
        (True, "%d.%m.%Y"),
        (True, "%d.%m. %H:%M:%S"),
        (True, "%d.%m. %H:%M"),
        (True, "%d.%m. %H"),
        (True, "%d.%m.")
    ]

    delta = None
    for includes_date, time_format in formats:
        try:
            date = datetime.strptime(time_str, time_format)
            now = datetime.now()
            if not includes_date:
                date = date.replace(year=now.year, month=now.month, day=now.day, microsecond=0)
            elif date.year == 1900:  # %d.%m. not setting year to 1970 but to 1900?
                date = date.replace(year=now.year)
            delta = datetime.now() - date
            break
        except ValueError:
            continue

    if not delta:
        minute = 0
        hour = 0
        day = 0
        week = 0
        args = time_str.split(' ')
        if len(args) > 0:
            for arg in args:
                try:
                    if 'h' in arg:
                        hour += int(arg.rstrip('h'))
                    elif 'm' in arg:
                        minute += int(arg.rstrip('m'))
                    elif 'w' in arg:
                        week += int(arg.rstrip('w'))
                    elif 'd' in arg:
                        day += int(arg.rstrip('d'))
                    else:
                        raise ValueError
                except ValueError:  # Make sure both cases are treated the same
                    raise ValueError(f'Invalid time argument: {arg}')
        delta = timedelta(hours=hour, minutes=minute, days=day, weeks=week)

    if not delta:
        raise ValueError(f'Invalid time argument: {time_str}')
    elif delta.total_seconds() <= 0:
        raise ValueError(f'Time delta can not be zero. {time_str}')

    return delta


def calc_xs_ys(data: List[Tuple[datetime, Balance]], percentage=False) -> Tuple[List[datetime], List[float]]:
    xs = []
    ys = []
    for time, balance in data:
        xs.append(time.replace(microsecond=0))
        if percentage:
            if data[0][1].amount > 0:
                amount = 100 * (balance.amount - data[0][1].amount) / data[0][1].amount
            else:
                amount = 0.0
        else:
            amount = balance.amount
        ys.append(round(amount, ndigits=CURRENCY_PRECISION.get(balance.currency, 3)))
    return xs, ys