"""Google Chat Card v2 sending tool."""

import json
import logging
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from tools.registry import registry, tool_error, tool_result

logger = logging.getLogger(__name__)


class CardHeader(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    subtitle: Optional[str] = None
    image_url: Optional[str] = None
    image_type: Literal["SQUARE", "CIRCLE"] = "SQUARE"
    image_alt_text: Optional[str] = None


class CardButton(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    action: str
    parameters: dict[str, str] = Field(default_factory=dict)


class CardSelectionItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    value: str
    selected: bool = False


class CardWidget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal[
        "text",
        "text_paragraph",
        "decorated_text",
        "buttons",
        "button_list",
        "selection",
        "selection_input",
        "image",
        "divider",
    ]
    text: Optional[str] = None
    image_url: Optional[str] = None
    alt_text: Optional[str] = None
    top_label: Optional[str] = None
    bottom_label: Optional[str] = None
    wrap_text: bool = True
    buttons: list[CardButton] = Field(default_factory=list)
    name: Optional[str] = None
    label: Optional[str] = None
    selection_type: Literal["RADIO_BUTTON", "CHECK_BOX", "SWITCH"] = "CHECK_BOX"
    items: list[CardSelectionItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_widget(self) -> "CardWidget":
        widget_type = self.type
        if widget_type in {"text", "text_paragraph", "decorated_text"} and not self.text:
            raise ValueError(f"{widget_type} widget requires text")
        if widget_type in {"buttons", "button_list"} and not self.buttons:
            raise ValueError("button widget requires at least one button")
        if widget_type in {"selection", "selection_input"}:
            if not self.name:
                raise ValueError("selection widget requires name")
            if not self.items:
                raise ValueError("selection widget requires at least one item")
        if widget_type == "image" and not self.image_url:
            raise ValueError("image widget requires image_url")
        return self


class CardSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    header: Optional[str] = None
    widgets: list[CardWidget]

    @model_validator(mode="after")
    def validate_selection_submit(self) -> "CardSection":
        selection_indexes = [
            index
            for index, widget in enumerate(self.widgets)
            if widget.type in {"selection", "selection_input"}
        ]
        for index in selection_indexes:
            if not any(
                widget.type in {"buttons", "button_list"}
                for widget in self.widgets[index + 1 :]
            ):
                raise ValueError(
                    "selection widgets must be followed by a submit button widget"
                )
        return self


class CardSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    card_id: str = "hermes-card"
    header: Optional[CardHeader] = None
    sections: list[CardSection]

    @model_validator(mode="after")
    def validate_sections(self) -> "CardSpec":
        if not self.sections:
            raise ValueError("card requires at least one section")
        return self


SEND_CHAT_CARD_SCHEMA = {
    "name": "send_chat_card",
    "description": (
        "Send a constrained Google Chat Card v2 message. Use only in Google Chat "
        "sessions when a compact interactive card is clearer than plain text."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "chat_id": {
                "type": "string",
                "description": "Google Chat space resource, e.g. spaces/AAAAabc123.",
            },
            "thread_id": {
                "type": "string",
                "description": "Optional Google Chat thread resource, e.g. spaces/AAAAabc123/threads/BBBB.",
            },
            "card": {
                "type": "object",
                "description": (
                    "Card spec with optional header and sections. Widget types: "
                    "text, decorated_text, image, divider, buttons, selection. "
                    "Selection widgets must be followed by a buttons widget."
                ),
            },
        },
        "required": ["chat_id", "card"],
    },
}


def _to_parameters(parameters: dict[str, str]) -> list[dict[str, str]]:
    return [
        {"key": str(key), "value": str(value)}
        for key, value in sorted(parameters.items())
    ]


def _button_to_chat(button: CardButton) -> dict[str, Any]:
    return {
        "text": button.text,
        "onClick": {
            "action": {
                "function": button.action,
                "parameters": _to_parameters(button.parameters),
            }
        },
    }


def _widget_to_chat(widget: CardWidget) -> dict[str, Any]:
    from gateway.platforms.googlechat import format_googlechat_markdown

    if widget.type in {"text", "text_paragraph"}:
        return {
            "textParagraph": {
                "text": format_googlechat_markdown(widget.text or "")
            }
        }
    if widget.type == "decorated_text":
        decorated: dict[str, Any] = {
            "text": format_googlechat_markdown(widget.text or ""),
            "wrapText": widget.wrap_text,
        }
        if widget.top_label:
            decorated["topLabel"] = widget.top_label
        if widget.bottom_label:
            decorated["bottomLabel"] = widget.bottom_label
        return {"decoratedText": decorated}
    if widget.type == "divider":
        return {"divider": {}}
    if widget.type == "image":
        image: dict[str, Any] = {"imageUrl": widget.image_url}
        if widget.alt_text:
            image["altText"] = widget.alt_text
        return {"image": image}
    if widget.type in {"buttons", "button_list"}:
        return {"buttonList": {"buttons": [_button_to_chat(btn) for btn in widget.buttons]}}
    if widget.type in {"selection", "selection_input"}:
        return {
            "selectionInput": {
                "name": widget.name,
                "label": widget.label or widget.name,
                "type": widget.selection_type,
                "items": [
                    {
                        "text": item.text,
                        "value": item.value,
                        "selected": item.selected,
                    }
                    for item in widget.items
                ],
            }
        }
    raise ValueError(f"unsupported widget type: {widget.type}")


def card_spec_to_cards_v2(card_spec: CardSpec | dict[str, Any]) -> dict[str, Any]:
    spec = (
        card_spec
        if isinstance(card_spec, CardSpec)
        else CardSpec.model_validate(card_spec)
    )
    card: dict[str, Any] = {
        "sections": [
            {
                **({"header": section.header} if section.header else {}),
                "widgets": [_widget_to_chat(widget) for widget in section.widgets],
            }
            for section in spec.sections
        ]
    }
    if spec.header:
        header: dict[str, Any] = {"title": spec.header.title}
        if spec.header.subtitle:
            header["subtitle"] = spec.header.subtitle
        if spec.header.image_url:
            header["imageUrl"] = spec.header.image_url
            header["imageType"] = spec.header.image_type
        if spec.header.image_alt_text:
            header["imageAltText"] = spec.header.image_alt_text
        card["header"] = header
    return {"cardId": spec.card_id, "card": card}


async def send_chat_card_tool(args, **_kw) -> str:
    chat_id = str(args.get("chat_id") or "").strip()
    if not chat_id:
        return tool_error("chat_id is required")
    try:
        card = card_spec_to_cards_v2(args.get("card") or {})
    except ValidationError as exc:
        return tool_error("invalid card spec", details=json.loads(exc.json()))
    except Exception as exc:  # noqa: BLE001
        return tool_error(f"invalid card spec: {exc}")

    try:
        from gateway.config import Platform, load_gateway_config
        from gateway.platforms.googlechat import (
            GoogleChatAdapter,
            check_googlechat_requirements,
        )

        config = load_gateway_config()
        pconfig = config.platforms.get(Platform.GOOGLECHAT)
        if not pconfig or not pconfig.enabled:
            return tool_error("Google Chat is not configured")
        if not check_googlechat_requirements(pconfig):
            return tool_error(
                "Google Chat requirements not met. Run: pip install 'hermes-agent[googlechat]' and configure service_account_json."
            )
        adapter = GoogleChatAdapter(pconfig)
        metadata = {"thread_id": str(args["thread_id"]).strip()} if args.get("thread_id") else None
        result = await adapter.send_card(chat_id, card, metadata=metadata)
    except Exception as exc:  # noqa: BLE001
        logger.warning("send_chat_card failed: %s", exc, exc_info=True)
        return tool_error(f"Google Chat card send failed: {type(exc).__name__}: {exc}")

    if not result.success:
        return tool_error(f"Google Chat card send failed: {result.error}")
    return tool_result(
        success=True,
        platform="googlechat",
        chat_id=chat_id,
        message_id=result.message_id,
        raw_response=result.raw_response,
    )


registry.register(
    name="send_chat_card",
    toolset="googlechat",
    schema=SEND_CHAT_CARD_SCHEMA,
    handler=send_chat_card_tool,
    is_async=True,
    emoji="🃏",
)
