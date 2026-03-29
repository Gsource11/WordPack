param(
    [int]$PointX = -2147483648,
    [int]$PointY = -2147483648,
    [int]$AncestorDepth = 5
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::UTF8

[void][Reflection.Assembly]::LoadWithPartialName("UIAutomationClient")
[void][Reflection.Assembly]::LoadWithPartialName("UIAutomationTypes")
[void][Reflection.Assembly]::LoadWithPartialName("WindowsBase")

$problematicClasses = @(
    "Chrome_RenderWidgetHostHWND",
    "Chrome_WidgetWin_1",
    "MozillaWindowClass",
    "ConsoleWindowClass"
)

function Emit-Json([hashtable]$payload) {
    $payload | ConvertTo-Json -Compress -Depth 4
}

function Get-RuntimeKey($element) {
    try {
        $runtimeId = $element.GetRuntimeId()
        if ($null -eq $runtimeId) {
            return $null
        }
        return ($runtimeId -join "-")
    } catch {
        return $null
    }
}

function Get-ControlTypeName($element) {
    try {
        return $element.Current.ControlType.ProgrammaticName
    } catch {
        return ""
    }
}

function Get-Stability($controlType, $className) {
    if ($controlType -in @("ControlType.Edit", "ControlType.Document")) {
        if ($className -in $problematicClasses) {
            return "conditional"
        }
        return "stable"
    }
    if ($controlType -in @("ControlType.Text", "ControlType.Pane", "ControlType.Custom")) {
        return "conditional"
    }
    return "unknown"
}

function New-Meta($element, $strategy, $reason) {
    if ($null -eq $element) {
        return @{
            source = "uia"
            reason = $reason
            detail = ""
            strategy = $strategy
            controlType = ""
            className = ""
            frameworkId = ""
            stability = "unknown"
            isPassword = $false
            text = ""
        }
    }

    $controlType = Get-ControlTypeName $element
    $className = ""
    $frameworkId = ""
    $isPassword = $false

    try { $className = [string]$element.Current.ClassName } catch {}
    try { $frameworkId = [string]$element.Current.FrameworkId } catch {}
    try { $isPassword = [bool]$element.Current.IsPassword } catch {}

    return @{
        source = "uia"
        reason = $reason
        detail = ""
        strategy = $strategy
        controlType = $controlType
        className = $className
        frameworkId = $frameworkId
        stability = Get-Stability $controlType $className
        isPassword = $isPassword
        text = ""
    }
}

function Try-GetSelectedText($element, $strategy) {
    $meta = New-Meta $element $strategy "no-textpattern"
    if ($null -eq $element) {
        return $meta
    }
    if ($meta.isPassword) {
        $meta.reason = "password-field"
        return $meta
    }

    try {
        $pattern = [System.Windows.Automation.TextPattern]$element.GetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern)
    } catch {
        return $meta
    }

    if ($null -eq $pattern) {
        return $meta
    }

    try {
        $ranges = $pattern.GetSelection()
    } catch {
        $meta.reason = "empty-selection"
        return $meta
    }

    if ($null -eq $ranges) {
        $meta.reason = "empty-selection-array"
        return $meta
    }

    $pieces = New-Object System.Collections.Generic.List[string]
    for ($i = 0; $i -lt $ranges.Count; $i++) {
        try {
            $text = [string]$ranges[$i].GetText(-1)
            if (![string]::IsNullOrWhiteSpace($text)) {
                [void]$pieces.Add($text.Trim())
            }
        } catch {
        }
    }

    $joined = ($pieces -join "`n").Trim()
    if ([string]::IsNullOrWhiteSpace($joined)) {
        $meta.reason = "empty-selection"
        return $meta
    }

    $meta.reason = "ok"
    $meta.text = $joined
    return $meta
}

try {
    $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
    $seen = New-Object System.Collections.Generic.HashSet[string]
    $candidates = New-Object System.Collections.Generic.List[object]

    if ($PointX -ne -2147483648 -and $PointY -ne -2147483648) {
        try {
            $pt = New-Object System.Windows.Point([double]$PointX, [double]$PointY)
            $pointElement = [System.Windows.Automation.AutomationElement]::FromPoint($pt)
            if ($null -ne $pointElement) {
                [void]$candidates.Add(@{ element = $pointElement; strategy = "point" })
            }
        } catch {
        }
    }

    try {
        $focused = [System.Windows.Automation.AutomationElement]::FocusedElement
        if ($null -ne $focused) {
            [void]$candidates.Add(@{ element = $focused; strategy = "focused" })
        }
    } catch {
    }

    if ($candidates.Count -eq 0) {
        Emit-Json @{ source = "uia"; reason = "no-focused-element"; detail = ""; strategy = "none"; controlType = ""; className = ""; frameworkId = ""; stability = "unknown"; isPassword = $false; text = "" }
        exit 0
    }

    $best = $null
    foreach ($candidate in $candidates) {
        $element = $candidate.element
        $strategy = [string]$candidate.strategy
        $depth = 0

        while ($null -ne $element -and $depth -le $AncestorDepth) {
            $runtimeKey = Get-RuntimeKey $element
            if (![string]::IsNullOrWhiteSpace($runtimeKey) -and !$seen.Add($runtimeKey)) {
                break
            }

            $currentStrategy = if ($depth -eq 0) { $strategy } else { "$strategy-parent-$depth" }
            $probe = Try-GetSelectedText $element $currentStrategy

            if ($probe.reason -eq "ok") {
                Emit-Json $probe
                exit 0
            }

            if ($null -eq $best) {
                $best = $probe
            } elseif ($best.stability -ne "stable" -and $probe.stability -eq "stable") {
                $best = $probe
            } elseif ($best.reason -eq "no-textpattern" -and $probe.reason -ne "no-textpattern") {
                $best = $probe
            }

            try {
                $element = $walker.GetParent($element)
            } catch {
                $element = $null
            }
            $depth++
        }
    }

    if ($null -eq $best) {
        Emit-Json @{ source = "uia"; reason = "no-uia-candidate"; detail = ""; strategy = "none"; controlType = ""; className = ""; frameworkId = ""; stability = "unknown"; isPassword = $false; text = "" }
        exit 0
    }

    Emit-Json $best
} catch {
    Emit-Json @{
        source = "uia"
        reason = "uia-script-error"
        detail = ([string]$_.Exception.Message)
        strategy = "none"
        controlType = ""
        className = ""
        frameworkId = ""
        stability = "unknown"
        isPassword = $false
        text = ""
    }
}
