import json
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="templates")
templates.env.filters["tojson"] = lambda valor: json.dumps(valor)
