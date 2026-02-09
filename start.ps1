Param(
    [switch]$NoFrontend
)

# 한글이 깨지지 않도록 콘솔을 UTF-8로 설정
$OutputEncoding = [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
chcp 65001 | Out-Null

$root = $PSScriptRoot

if ($NoFrontend) {
    # 백엔드만 현재 터미널에서 실행 (Ctrl+C 로 종료)
    Write-Host "==== 자동매매 백엔드 서버 시작 (현재 터미널) ====" -ForegroundColor Cyan
    Write-Host "종료: Ctrl+C" -ForegroundColor Yellow
    Set-Location $root
    uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
} else {
    # 백엔드는 백그라운드 잡으로, 프론트는 현재 터미널에서 실행. Ctrl+C 시 둘 다 종료
    Write-Host "==== 자동매매 백엔드 서버 시작 (백그라운드) ====" -ForegroundColor Cyan
    $backendJob = Start-Job -ScriptBlock {
        Set-Location $using:root
        uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
    }

    Write-Host "==== 프론트엔드 대시보드 시작 (현재 터미널) ====" -ForegroundColor Cyan
    Write-Host "백엔드: http://localhost:8000 | 프론트: http://localhost:5173" -ForegroundColor Green
    Write-Host "종료: Ctrl+C (백엔드/프론트 둘 다 종료됨)" -ForegroundColor Yellow
    Write-Host ""

    try {
        Set-Location (Join-Path $root "frontend")
        npm run dev
    } finally {
        Write-Host "백엔드 종료 중..." -ForegroundColor Yellow
        Stop-Job $backendJob -ErrorAction SilentlyContinue
        Remove-Job $backendJob -Force -ErrorAction SilentlyContinue
        Set-Location $root
    }
}
