"""The brain: sends your question to Gemini or Claude with Jarvis's tools.

Switch providers with llm.provider in config.yaml. Both get the same tools,
so answers about weather, traffic and news always use live numbers.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime

from data import DataHub

log = logging.getLogger("jarvis.llm")

SYSTEM = """You are Jarvis, a warm, concise household voice assistant on a wall display.
Your replies are spoken aloud, so:
- Answer in 1 to 3 short sentences unless asked for more.
- Plain speech only: no markdown, lists, emoji, URLs or code.
- Say numbers naturally ("about 18 minutes", "41 degrees").
Use the tools for anything about weather, commute or traffic, and news; never guess those.
For other questions (general knowledge, homework help, recipes, ideas) answer from your own knowledge,
and say so honestly if something may be out of date or you are not sure.
This assistant is for home and family use only.
Current local time: {now}. Household member's name: {name}. Known places: {places}."""

MAX_HISTORY = 6  # remember the last few exchanges for follow-ups like "and tomorrow?"


class Brain:
    def __init__(self, cfg: dict, hub: DataHub):
        self.cfg = cfg
        self.hub = hub
        self.history: list[tuple[str, str]] = []
        self.provider = cfg.get("llm", {}).get("provider", "gemini")

    # ----- tools (shared by both providers) -----
    def get_weather(self) -> dict:
        """Get current weather, today's high and low, rain chances, the next hours, and tomorrow's forecast at home."""
        return self.hub.weather()

    def get_commute(self, place_id: str) -> dict:
        """Get live driving time with traffic from home to a saved place, and when to leave.

        Args:
            place_id: id of a saved place, for example "school" or "gym".
        """
        return self.hub.commute(place_id)

    def get_news(self) -> list:
        """Get the latest news headlines from the household's chosen news feeds."""
        return self.hub.news()

    def _run_tool(self, name: str, args: dict):
        fn = {"get_weather": self.get_weather, "get_commute": self.get_commute,
              "get_news": self.get_news}.get(name)
        if not fn:
            return {"error": f"unknown tool {name}"}
        try:
            return fn(**args)
        except Exception as e:
            log.exception("tool %s failed", name)
            return {"error": str(e)}

    def _system(self) -> str:
        places = ", ".join(f'{p["id"]} ({p["label"]})' for p in self.hub.places()) or "none"
        now = datetime.now(self.hub.tz).strftime("%A, %B %-d, %Y, %-I:%M %p")
        return SYSTEM.format(now=now, name=self.cfg["home"].get("name", "friend"), places=places)

    # ----- public -----
    def ask(self, question: str) -> str:
        try:
            if self.provider == "claude":
                answer = self._ask_claude(question)
            else:
                answer = self._ask_gemini(question)
        except KeyError as e:
            log.error("missing setting: %s", e)
            return f"I'm not set up yet. Please add the {e.args[0]} to the dot env file."
        except Exception as e:
            log.exception("LLM call failed")
            return f"Sorry, I couldn't reach my {self.provider} brain just now."
        answer = (answer or "").strip() or "Sorry, I didn't catch an answer for that."
        self.history.append((question, answer))
        self.history = self.history[-MAX_HISTORY:]
        return answer

    # ----- Gemini (google-genai SDK, automatic function calling) -----
    def _ask_gemini(self, question: str) -> str:
        from google import genai
        from google.genai import types

        g = self.cfg["llm"].get("gemini", {})
        if not hasattr(self, "_gclient"):
            if g.get("use_vertex"):
                self._gclient = genai.Client(vertexai=True, project=g["project"],
                                             location=g.get("location", "us-central1"))
            else:
                self._gclient = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        contents = []
        for q, a in self.history:
            contents.append(types.Content(role="user", parts=[types.Part(text=q)]))
            contents.append(types.Content(role="model", parts=[types.Part(text=a)]))
        contents.append(types.Content(role="user", parts=[types.Part(text=question)]))
        resp = self._gclient.models.generate_content(
            model=g.get("model", "gemini-flash-latest"),
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=self._system(),
                tools=[self.get_weather, self.get_commute, self.get_news],
                max_output_tokens=400,
                temperature=0.6,
            ),
        )
        return resp.text

    # ----- Claude (anthropic SDK, manual tool loop) -----
    CLAUDE_TOOLS = [
        {"name": "get_weather", "description": "Current weather, today's high/low, rain chances, next hours and tomorrow at home.",
         "input_schema": {"type": "object", "properties": {}}},
        {"name": "get_commute", "description": "Live driving time with traffic from home to a saved place, and when to leave.",
         "input_schema": {"type": "object", "properties": {"place_id": {"type": "string", "description": "Saved place id, e.g. school or gym"}},
                          "required": ["place_id"]}},
        {"name": "get_news", "description": "Latest headlines from the household's news feeds.",
         "input_schema": {"type": "object", "properties": {}}},
    ]

    def _ask_claude(self, question: str) -> str:
        import anthropic

        if not hasattr(self, "_aclient"):
            self._aclient = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
        model = self.cfg["llm"].get("claude", {}).get("model", "claude-haiku-5-5")
        messages = []
        for q, a in self.history:
            messages += [{"role": "user", "content": q}, {"role": "assistant", "content": a}]
        messages.append({"role": "user", "content": question})
        for _ in range(5):  # at most 5 tool rounds
            resp = self._aclient.messages.create(model=model, max_tokens=400, system=self._system(),
                                                 tools=self.CLAUDE_TOOLS, messages=messages)
            if resp.stop_reason != "tool_use":
                return "".join(b.text for b in resp.content if b.type == "text")
            messages.append({"role": "assistant", "content": resp.content})
            results = []
            for b in resp.content:
                if b.type == "tool_use":
                    out = self._run_tool(b.name, b.input or {})
                    results.append({"type": "tool_result", "tool_use_id": b.id,
                                    "content": json.dumps(out)})
            messages.append({"role": "user", "content": results})
        return "Sorry, that took too many steps. Try asking a simpler way."
