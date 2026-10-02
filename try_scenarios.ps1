# Sends every row of scenarios.csv to the running service and prints what came back.
# Usage (server must be running):  .\try_scenarios.ps1
#                                  .\try_scenarios.ps1 -Group 4-critical
#                                  .\try_scenarios.ps1 -DelaySeconds 20   # pace for the Groq free tier
param(
    [string]$BaseUrl = "http://127.0.0.1:8000",
    [string]$Group = "",
    [double]$DelaySeconds = 3
)

$rows = Import-Csv -Path (Join-Path $PSScriptRoot "scenarios.csv") -Encoding UTF8
if ($Group) { $rows = $rows | Where-Object { $_.group -eq $Group } }

$results = @()
$i = 0
foreach ($row in $rows) {
    $i++
    $body = @{ text = $row.text; shipmentId = "SCN-$i"; requestId = "scn-$i" } | ConvertTo-Json -Compress
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($body)
    $started = Get-Date
    try {
        $r = Invoke-RestMethod -Method Post -Uri "$BaseUrl/v1/exceptions:analyze" `
            -ContentType "application/json; charset=utf-8" -Body $bytes
        $cat = $r.structuredRecord.category
        $sev = $r.structuredRecord.severity
        $src = $r.analysisSource
        $review = $r.structuredRecord.needsReview
        $reasoning = $r.reasoning
    } catch {
        $cat = "ERROR"; $sev = "ERROR"; $src = "-"; $review = "-"
        $reasoning = $_.ErrorDetails.Message
        if (-not $reasoning) { $reasoning = $_.Exception.Message }
    }
    $seconds = [math]::Round(((Get-Date) - $started).TotalSeconds, 2)

    $catOk = ($row.expected_category -split '\|') -contains $cat
    if ($row.group -eq "6-not-critical") { $sevOk = ($sev -ne "CRITICAL") -and ($sev -ne "ERROR") }
    else { $sevOk = $sev -eq $row.expected_severity }

    $results += [pscustomobject]@{
        n = $i; group = $row.group; text = $row.text
        category = $cat; expectedCategory = $row.expected_category; catOk = $catOk
        severity = $sev; expectedSeverity = $row.expected_severity; sevOk = $sevOk
        needsReview = $review; source = $src; seconds = $seconds
        why = $row.why; reasoning = $reasoning
    }

    $color = if ($catOk -and $sevOk) { "Green" } elseif ($catOk -or $sevOk) { "Yellow" } else { "Red" }
    Write-Host ("[{0}] {1} | {2}/{3} (expected {4}/{5}) | {6} | {7}s" -f `
        $i, $row.group, $cat, $sev, $row.expected_category, $row.expected_severity, $src, $seconds) -ForegroundColor $color
    Write-Host ("     {0}" -f $row.why) -ForegroundColor DarkGray

    if ($i -lt $rows.Count) { Start-Sleep -Seconds $DelaySeconds }
}

$n = $results.Count
$catHits = ($results | Where-Object catOk).Count
$sevHits = ($results | Where-Object sevOk).Count
$critRows = $results | Where-Object { $_.expectedSeverity -eq "CRITICAL" }
$critHits = ($critRows | Where-Object { $_.severity -eq "CRITICAL" }).Count
$llm = ($results | Where-Object { $_.source -eq "LLM" }).Count

Write-Host ""
Write-Host "Category correct: $catHits / $n"
Write-Host "Severity correct: $sevHits / $n"
Write-Host "CRITICAL recall:  $critHits / $($critRows.Count)"
Write-Host "Answered by LLM:  $llm / $n  (the rest = keyword fallback or error)"

$outDir = Join-Path $PSScriptRoot "eval_results"
$out = Join-Path $outDir ("scenarios_{0}.csv" -f (Get-Date -Format "yyyyMMdd_HHmmss"))
$results | Export-Csv -Path $out -NoTypeInformation -Encoding UTF8
Write-Host "Full results (with the model's reasoning): $out"
