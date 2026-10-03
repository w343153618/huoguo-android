"""Validate client settings before touching the running Android session."""
MIN_BIT_RATE = 500_000
MAX_BIT_RATE = 40_000_000  # VideoToolbox path; the legacy Android encoder remains limited to 12 Mbps.
DEFAULT_BIT_RATE = 2_500_000
# Standard 16:9 presets plus accepted historical long-edge settings. The
# hardware worker CLI shares these values so authentication and launch agree.
RESOLUTION_MAX_SIZES = (768, 960, 1200, 1280, 1600, 1920, 2400)

def parse_settings(settings, default_size):
    if not isinstance(settings, dict):
        raise ValueError('Invalid settings object')
    size = settings.get('max_size', default_size)
    bitrate = settings.get('video_bit_rate', DEFAULT_BIT_RATE)
    if type(size) is not int or size not in RESOLUTION_MAX_SIZES:
        raise ValueError('Invalid resolution')
    if type(bitrate) is not int or not MIN_BIT_RATE <= bitrate <= MAX_BIT_RATE:
        raise ValueError('Video bitrate must be 500000 to 40000000 bits/s')
    return size, bitrate


def parse_bitrate_mode(settings):
    mode=settings.get('bitrate_mode','CBR')
    if type(mode) is not str or mode not in ('CBR','VBR','ADAPTIVE_VBR'):
        raise ValueError('Supported bitrate modes: CBR, VBR, ADAPTIVE_VBR')
    return mode, {'CBR':2, 'VBR':1, 'ADAPTIVE_VBR':1}[mode]


def parse_max_fps(settings):
    fps=settings.get('max_fps',60)
    if type(fps) is not int or fps not in (30,60,120):
        raise ValueError('Supported frame-rate limits: 30, 60, 120')
    return fps
