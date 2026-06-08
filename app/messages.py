# -*- coding: utf-8 -*-
"""Korean UI strings (UTF-8). Keep user-facing text here."""

from app.config import settings

BOT_NAME = settings.bot_display_name
USER_CMD = "회식"

WELCOME_TEXT = (
    f"안녕하세요, *{BOT_NAME}* 입니다.\n"
    "이 채널에서 회식 일정을 자동으로 관리합니다.\n"
    f"• `/{USER_CMD}` — 설정/상태/실행 패널\n"
    f"• `/{USER_CMD} 설정` — 설정 모달\n"
    f"• `/{USER_CMD} 취소` — 진행 중인 회식 흐름 취소\n"
    "• *일정 확인* 버튼으로 다음 실행 시각을 볼 수 있습니다."
)

BTN_SETTINGS = "설정"
BTN_STATUS = "일정 확인"
BTN_START_NOW = "지금 투표 시작"
BTN_CANCEL_RUN = "진행 취소"

MODAL_TITLE = "회식 일정 설정"
MODAL_SAVE = "저장"
MODAL_CANCEL = "취소"
MODAL_HELP = (
    "*자동 실행*은 아래 주기·시각에 맞춰 투표를 엽니다.\n"
    "*투표 날짜 버튼*은 오늘~이번 달 말까지 *영업일(월~금)* 전체입니다.\n"
    "*투표 기간*은 최소 12시간입니다. 너무 짧으면 열리자마자 마감됩니다."
)

LABEL_AUTOMATIC_EXECUTION = "자동 실행"
LABEL_SCHEDULE_TYPE = "자동 실행 주기"
LABEL_WEEKDAY = "요일"
LABEL_DAY = "날짜 (1–28)"
LABEL_NTH = "번째 주 (마지막=-1)"
LABEL_MONTH_INTERVAL = "개월 간격 (1–12)"
LABEL_HOUR = "자동 실행 시각 (0–23시)"
LABEL_POLL_HOURS = "투표 마감까지 (시간, 12–168)"
LABEL_BOOKING_URL = "예약 링크 (선택)"

OPT_AUTOMATIC_ON = "사용"
OPT_AUTOMATIC_OFF = "사용 안 함"
OPT_WEEKLY = "매주 요일"
OPT_MONTHLY_DAY = "매월 n일"
OPT_MONTHLY_NTH = "매월 n번째 요일"
LEGACY_WEEKLY_NOTICE = "기존 매주 요일 자동 실행은 새 설정에서 월간 주기로 전환됩니다."

WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]
WEEKDAYS_ALL = WEEKDAYS

MSG_NO_SCHEDULE = (
    f"아직 일정이 없습니다. `/{USER_CMD}` 을 입력하거나 "
    f"*설정* 버튼으로 설정해 주세요."
)
MSG_SAVED = "설정을 저장했습니다."
MSG_STATUS_HEADER = f"*{BOT_NAME} 일정*"
MSG_SETTINGS_PROMPT = f"`{USER_CMD}` 설정과 실행을 아래 버튼으로 진행하세요."
MSG_USE_SETTINGS_BUTTON = f"설정 모달은 `/{USER_CMD} 설정` 또는 *설정* 버튼으로 여세요."
MSG_ADMIN_ONLY = "관리자만 이 작업을 실행할 수 있습니다."

MSG_CHANNEL_DISABLED = "채널이 등록되지 않았거나 비활성화되었습니다."
MSG_POLL_ALREADY_OPEN = (
    "이미 진행 중인 회식 일정 투표가 있습니다. "
    "새 투표가 필요하면 `/회식 지금 강제` 또는 `/회식 run-now replace`를 사용하세요."
)
MSG_NO_POLL_DATES = "이번 달 남은 영업일이 없어 투표를 열 수 없습니다."
MSG_SETTINGS_INVALID = "설정 값이 올바르지 않습니다. 투표 기간(12–168시간)과 주기를 확인해 주세요."
MSG_POLL_STARTED = "이번 달 회식 날짜 투표가 시작되었습니다."
MSG_POLL_START_REQUESTED = "회식 일정 투표를 시작했습니다."
MSG_POLL_CLOSED = "마감된 투표입니다."
MSG_ACTION_INVALID = "요청 값이 올바르지 않습니다. 최신 메시지의 버튼을 다시 눌러 주세요."
MSG_INVALID_POLL_OPTION = "투표 후보에 없는 날짜입니다. 최신 투표 메시지의 날짜 버튼을 눌러 주세요."
MSG_NO_VOTES_SKIP = "투표 참여가 없어 이번 회차를 건너뜁니다."
MSG_NO_ASSIGNEE = "예약 담당자를 지정할 멤버가 없습니다."
MSG_BOOKING_URL_MISSING = "(예약 링크 미설정)"
MSG_BOOKING_DM_TITLE = "*회식 예약 담당*으로 선택되었습니다."
MSG_BOOKING_DONE_BTN = "예약 완료"
MSG_WORKFLOW_NOT_FOUND = "워크플로를 찾을 수 없습니다."
MSG_ALREADY_DONE = "이미 완료 처리되었습니다."
MSG_ONLY_ASSIGNEE = "예약 담당자만 완료할 수 있습니다."
MSG_BOOKING_NOT_READY = "아직 예약 담당 단계가 아닙니다. 투표가 끝난 뒤 담당자에게 전달된 DM에서 예약 완료를 눌러 주세요."
MSG_BOOKING_DONE_OK = "예약 완료로 표시했습니다."
MSG_NO_ACTIVE_RUN = "취소할 진행 중인 회식 일정이 없습니다."
MSG_RUN_CANCELLED = "진행 중인 회식 일정이 취소되었습니다."
