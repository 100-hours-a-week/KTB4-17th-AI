from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class ApiModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,  # Pydantic은 별도의 설정 없이 바로 사용할 수 있는 세 가지 내장 별칭 생성기를 제공합니다.
        validate_by_name=True,  # 별칭으로 채우기 == populate_by_name (2.11 부터 안쓰기를 권장)
        validate_by_alias=True,  # 유효성 검사
        extra="forbid",  # allow -추가속성 허용/forbid- 추가속성 금지/ignore- 추가속성 무시
    )


# Enum
