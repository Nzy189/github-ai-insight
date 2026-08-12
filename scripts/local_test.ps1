# ============================================================
# 本地一键验证 (Windows / PowerShell)
#   .\scripts\local_test.ps1          完整验证
#   .\scripts\local_test.ps1 -Serve   验证完再起 HTTP 服务
# 不需要任何 API Key，不发起任何网络请求。
# ============================================================
param([switch]$Serve, [int]$Port = 8080)

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

function Step($n, $msg) { Write-Host "`n[$n] $msg" -ForegroundColor Cyan }

Step 1 "安装依赖"
python -m pip install -q -r requirements-dev.txt

Step 2 "配置自检"
python main.py --show-config

Step 3 "单元测试"
python -m pytest -q
if ($LASTEXITCODE -ne 0) { Write-Host "测试失败，终止。" -ForegroundColor Red; exit 1 }

Step 4 "Mock 全链路（抓取 → 分析 → 打分 → 报告 → 归档 → 推送预览）"
Remove-Item -Recurse -Force .\data-local -ErrorAction SilentlyContinue
python main.py --now --mock
if ($LASTEXITCODE -ne 0) { Write-Host "Mock 执行失败。" -ForegroundColor Red; exit 1 }

Step 5 "去重验证（第二次执行应换一个项目）"
python main.py --now --mock | Select-String "胜出"

Step 6 "数据库记录"
python main.py --list --data-dir .\data-local

Step 7 "产物清单"
Get-ChildItem -Recurse .\data-local -File | Select-Object FullName, Length | Format-Table -AutoSize

Write-Host "`n✅ 本地验证全部通过" -ForegroundColor Green
$report = Get-ChildItem .\data-local\reports\*.html | Sort-Object LastWriteTime -Descending | Select-Object -First 1
Write-Host "   最新报告: $($report.FullName)"

if ($Serve) {
    Write-Host "`n启动报告服务: http://localhost:$Port/reports  (Ctrl+C 停止)" -ForegroundColor Yellow
    python main.py --serve --data-dir .\data-local --port $Port
} else {
    Write-Host "   在浏览器中查看: python main.py --serve --data-dir ./data-local"
}
