# 한글이 깨지지 않도록 콘솔을 UTF-8로 설정
$OutputEncoding = [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
chcp 65001 | Out-Null

function Stop-ByPort {
    param(
        [Parameter(Mandatory = $true)]
        [int]$Port
    )

    try {
        $connections = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue
        if (-not $connections) {
            Write-Host "포트 $Port 에서 실행 중인 프로세스가 없습니다." -ForegroundColor DarkGray
            return
        }

        $pids = $connections | Select-Object -ExpandProperty OwningProcess -Unique
        foreach ($pid in $pids) {
            try {
                $proc = Get-Process -Id $pid -ErrorAction SilentlyContinue
                if ($null -ne $proc) {
                    Write-Host "포트 $Port 사용 중인 프로세스 종료: $($proc.ProcessName) (PID=$pid)" -ForegroundColor Yellow
                    Stop-Process -Id $pid -Force
                }
            } catch {
                Write-Host "PID $pid 종료 중 오류: $_" -ForegroundColor Red
            }
        }
    } catch {
        Write-Host "포트 $Port 확인 중 오류: $_" -ForegroundColor Red
    }
}

Write-Host "==== 자동매매/프론트엔드 서버 종료 ====" -ForegroundColor Cyan

# FastAPI(Uvicorn) 기본 포트
Stop-ByPort -Port 8000

# Vite 프론트엔드 기본 포트
Stop-ByPort -Port 5173

Write-Host "종료 요청이 완료되었습니다." -ForegroundColor Green

