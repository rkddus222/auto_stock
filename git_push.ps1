Param(
    [string]$Message = ""
)

# 한글이 깨지지 않도록 콘솔을 UTF-8로 설정
$OutputEncoding = [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
chcp 65001 | Out-Null

if (-not $Message) {
    $now = Get-Date -Format "yyyy-MM-dd HH:mm"
    $Message = "chore: auto commit ($now)"
}

Write-Host "==== Git 자동 커밋 & 푸시 ====" -ForegroundColor Cyan
Write-Host "메시지: $Message" -ForegroundColor Yellow

Write-Host "`n변경 사항 요약:" -ForegroundColor Cyan
git status

Write-Host "`n스테이지에 추가 중 (git add .)..." -ForegroundColor Cyan
git add .

try {
    Write-Host "커밋 실행 중..." -ForegroundColor Cyan
    git commit -m "$Message"
} catch {
    Write-Host "커밋 실패 또는 커밋할 변경 사항이 없습니다: $_" -ForegroundColor Yellow
}

Write-Host "원격 저장소로 푸시 중 (git push)..." -ForegroundColor Cyan
git push

Write-Host "완료되었습니다." -ForegroundColor Green

