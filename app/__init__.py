"""gpu-monitor — 노드별 GPU 할당(allocation) 현황 대시보드.

model-monitor(LiteLLM 서빙 모니터)의 형제 프로젝트. 다만 축이 다르다:
  model-monitor: LiteLLM model_name -> api_base -> backend
  gpu-monitor:   Node -> GPU -> Workload(할당)

할당(누가 nvidia.com/gpu 를 몇 개 점유했나)만 다룬다. 실사용률(DCGM %/VRAM)이 아니다.
수집 계층(app.core/app.services)은 표준 라이브러리만 쓴다(에어갭). 웹 계층만 FastAPI.
"""

__version__ = "0.3.0"
