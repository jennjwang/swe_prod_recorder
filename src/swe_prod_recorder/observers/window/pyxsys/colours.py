from platform import system as platform

# Code reused from mvdef library (it's my code I might as well!)
# via https://github.com/lmmx/mvdef/src/colours.py


def get_colour_codes(colour=None):
    """
    Return a dictionary of ANSI colour codes if no colour specified,
    if a colour is given then check it is in the list of keys and
    return a start and end ANSI code.
    """
    cols = [
        ["black", "\033[0;30m"],
        ["blue", "\033[0;34m"],
        ["brown", "\033[0;33m"],
        ["cyan", "\033[0;36m"],
        ["dark_gray", "\033[1;30m"],
        ["green", "\033[0;32m"],
        ["light_blue", "\033[1;34m"],
        ["light_cyan", "\033[1;36m"],
        ["light_gray", "\033[0;37m"],
        ["light_green", "\033[1;32m"],
        ["light_purple", "\033[1;35m"],
        ["light_red", "\033[1;31m"],
        ["light_white", "\033[1;37m"],
        ["purple", "\033[0;35m"],
        ["red", "\033[0;31m"],
        ["yellow", "\033[1;33m"],
    ]
    ending = "\033[0m"
    colour_dict = dict([[x[0], {"on": x[1], "off": ending}] for x in sorted(cols)])
    if colour is None:
        return colour_dict
    else:
        assert colour in colour_dict, f"{colour} not in {list(colour_dict.keys())}"
        return colour_dict.get(colour)


def get_effect_codes(effect=None):
    """
    Return a dictionary of ANSI text effect codes if no effect specified,
    if a text effect is given then check it is in the list of keys and return
    a start and end ANSI code.
    """
    effects = [
        ["bold", "\033[1m"],
        ["faint", "\033[2m"],
        ["italic", "\033[3m"],
        ["underline", "\033[4m"],
        ["blink", "\033[5m"],
        ["negative", "\033[7m"],
        ["crossed", "\033[9m"],
    ]
    ending = "\033[0m"
    effect_dict = dict([[x[0], {"on": x[1], "off": ending}] for x in sorted(effects)])
    if effect is None:
        return effect_dict
    else:
        assert effect in effect_dict, f"{effect} not in {list(effect_dict.keys())}"
        return effect_dict.get(effect)


def colour_str(colour, text, end=True):
    """
    Apply the ANSI colour code to a string, and optionally append the end code.
    Only use `end=False` when combining ANSI codes (i.e. in which case the end
    code would apply to all modifiers so as to prevent their combination).
    """
    if platform().lower() not in ["linux", "darwin"]:
        return text
    colour_on, colour_off = get_colour_codes(colour).values()
    colourful = f"{colour_on}{text}{colour_off if end else ''}"
    return colourful


def effect_str(effect, text, end=True):
    """
    Apply the ANSI effect code to a string, and optionally append the end code.
    Only use `end=False` when combining ANSI codes (i.e. in which case the end
    code would apply to all modifiers so as to prevent their combination).
    """
    if platform().lower() not in ["linux", "darwin"]:
        return text
    effect_on, effect_off = get_effect_codes(effect).values()
    effectful = f"{effect_on}{text}{effect_off if end else ''}"
    return effectful


def underline(text, end=True):
    """
    Apply the underline effect ANSI code to a text string.
    """
    return effect_str("underline", text, end)


def colour_effect_str(colour, effect, text):
    """
    Apply multiple ANSI colour codes to a string, ending only the outer one,
    e.g. to both underline and colour, or underline and bold, etc.

    The effect must be applied after a colour code, otherwise it will be
    'overridden' by the colour code (as far as I have tested).

    Note that multiple effects seem to cancel out (with the latter ANSI code
    taking priority, 'overriding' the other), i.e. multi-effect not possible.
    """
    if platform().lower() not in ["linux", "darwin"]:
        return text
    effectful = effect_str(effect, text, end=False)
    combo = colour_str(colour, effectful)
    return combo
