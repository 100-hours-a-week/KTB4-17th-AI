# 태깅·추출 SLM 서빙 이미지 — RunPod 템플릿의 Container Image 로 쓴다.
# 앱 이미지(루트 Dockerfile)와 따로 빌드·배포한다. GPU 서버에서만 돈다.
#
# Apple Silicon Mac 에서 빌드할 때는 반드시 linux/amd64 로 (RunPod GPU 서버는 x86_64):
#   docker buildx build --platform linux/amd64 -f serving/vllm/Dockerfile -t <registry>/ktb-slm-vllm:<tag> --push serving/vllm
#
# 드라이버가 CUDA 13 을 못 받는 GPU(드라이버 < 580)면 베이스를 v0.30.0-cu129 로 바꾼다.
FROM vllm/vllm-openai:v0.30.0

WORKDIR /serving
COPY config.yaml start.sh ./
RUN chmod +x start.sh

EXPOSE 8000
# 공식 이미지의 ENTRYPOINT(vllm serve)를 대신한다. 어댑터 준비·검증 후 vllm serve 를 exec 한다
ENTRYPOINT ["/serving/start.sh"]
