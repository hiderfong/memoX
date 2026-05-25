"""PreferenceLearner 单元测试"""

import asyncio
import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from memory.preference_learner import PreferenceLearner
from storage.persistence import PersistenceStore


class AsyncPreferenceProvider:
    async def chat(self, messages, model=None, temperature=None, max_tokens=None):
        response = MagicMock()
        response.content = '{"preferences":[{"content":"用户偏好简洁回答","importance":5}]}'
        return response


def test_extract_and_save_async_provider(tmp_path):
    store = PersistenceStore(tmp_path / "test.db")
    learner = PreferenceLearner(store)

    count = asyncio.run(
        learner.extract_and_save_async(
            messages=[
                {"role": "user", "content": "请以后回答简洁一点"},
                {"role": "assistant", "content": "好的。"},
            ],
            llm_provider=AsyncPreferenceProvider(),
        )
    )

    assert count == 1
    prefs = learner.get_preferences()
    assert len(prefs) == 1
    assert prefs[0]["category"] == "preference"
    assert prefs[0]["importance"] == 5
