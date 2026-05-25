import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))
from pydantic import TypeAdapter

from workflow.dsl import Workflow

try:
    schema = TypeAdapter(Workflow).json_schema()
    print(json.dumps(schema, indent=2))
except Exception as e:
    print(e)
