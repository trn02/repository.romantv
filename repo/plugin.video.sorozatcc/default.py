from __future__ import unicode_literals

import sys
import urllib.parse

from main import router


if __name__ == "__main__":
    router(dict(urllib.parse.parse_qsl(sys.argv[2][1:])))
