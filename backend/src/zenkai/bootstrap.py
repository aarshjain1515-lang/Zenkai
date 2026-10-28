from pathlib import Path
import shutil

from .config import settings
from .connector import initialise_gateway

initialise_gateway(settings())
Path("/scripts").mkdir(exist_ok=True)
shutil.copyfile("/app/loadtests/traffic.js", "/scripts/traffic.js")
print("Demo gateway and k6 script volumes prepared.")
