import re

# "금칙어 목록은 rules.py의 짧은 상수로 둔다."
PROFANITY_WORDS = ["씨발", "개새끼", "병신", "지랄", "좆"]

# 정체성 정규식
IDENTITY_REGEX = re.compile(
    r"(?:저는|나는|난|제가)\s*(?:AI|인공지능|언어\s*모델|챗봇)(?:\s*모델)?\s*(?:입니다|이에요|예요|이라|야)|"
    r"(?:AI|인공지능)\s*모델\s*(?:로서|입니다|이에요)|"
    r"인공지능이라|"
    r"도움이 필요하시면|"
    r"무엇을 도와드릴까요"
)

# 사실 (과거 회상) 정규식
PAST_MEETING_REGEX = re.compile(r"(저번|지난\s?번)[^.?!\n]*(말씀|얘기|이야기|하셨잖|하신|뵀)")

# 사실 (근거 없는 언급) 정규식
UNSAID_TRIGGER_REGEX = re.compile(r"하셨잖아요|했다고 하셨|말씀하셨잖아요")

# 작업 (대본) 정규식: 줄 시작이 1~12자 + 콜론(： 또는 :) + 공백 + 글자인 줄
SCRIPT_LINE_REGEX = re.compile(r"^.{1,12}[：:]\s+\S+", re.MULTILINE)

# 이모지 정규식 (간단한 버전)
EMOJI_REGEX = re.compile(r"[\U00010000-\U0010ffff]", flags=re.UNICODE)

# 반말 정규식
SPEECH_END_REGEX = re.compile(r"(이야|거든|잖아)[.?!]*$")

# 존댓말 정규식
FORMAL_END_REGEX = re.compile(r"(요|니다)[.?!]*$")
