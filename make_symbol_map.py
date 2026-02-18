import json
from pathlib import Path

import FinanceDataReader as fdr


def generate_ts_file() -> None:
  print("⏳ KOSPI, KOSDAQ 전 종목 데이터를 가져오는 중...")

  # 1. KOSPI, KOSDAQ 종목 리스트 가져오기
  kospi = fdr.StockListing("KOSPI")
  kosdaq = fdr.StockListing("KOSDAQ")

  # 2. 필요한 데이터만 추출 (Code, Name)
  df_kospi = kospi[["Code", "Name"]]
  df_kosdaq = kosdaq[["Code", "Name"]]

  # 3. 딕셔너리로 변환 {'005930': '삼성전자', ...}
  symbol_map: dict[str, str] = {}

  for _, row in df_kospi.iterrows():
      code = str(row["Code"]).strip()
      name = str(row["Name"]).strip()
      if code and name:
          symbol_map[code] = name

  for _, row in df_kosdaq.iterrows():
      code = str(row["Code"]).strip()
      name = str(row["Name"]).strip()
      if code and name:
          symbol_map[code] = name

  print(f"✅ 총 {len(symbol_map)}개 종목 매핑 완료.")

  # 4. TypeScript 파일 포맷으로 저장
  #   frontend/src/constants/stockMap.ts 로 바로 생성/덮어쓰도록 설정
  project_root = Path(__file__).resolve().parent
  ts_path = project_root / "frontend" / "src" / "constants" / "stockMap.ts"
  ts_path.parent.mkdir(parents=True, exist_ok=True)

  ts_content = (
      "export const symbolNames: Record<string, string> = "
      f"{json.dumps(symbol_map, ensure_ascii=False, indent=2)};\n"
  )

  with ts_path.open("w", encoding="utf-8") as f:
      f.write(ts_content)

  print(f"🎉 'stockMap.ts' 파일 생성/업데이트 완료 → {ts_path}")


if __name__ == "__main__":
  generate_ts_file()

