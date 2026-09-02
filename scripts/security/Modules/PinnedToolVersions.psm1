# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
# cspell:ignore pscustomobject
#Requires -Version 7.0

<#
.SYNOPSIS
    Defines pin discovery functions used by binary freshness checks.
.DESCRIPTION
    Enumerates tracked shell, PowerShell, JSON, and JSONC source files, extracts
    supported literal version assignments, and classifies them against an
    upstream release.
#>

$script:PinRegexTimeout = [timespan]::FromSeconds(2)
$script:PinShellLeadingAssignmentPattern =
    '(?:[A-Za-z_][A-Za-z0-9_]*\s*=\s*(?:"(?:\\.|[^"\\])*"|''[^'']*''|[^\s]+)\s+)*'
$script:PinShellCommandPrefixPattern =
    '(?:(?:(?:if|then|else|elif|do|env|command|sudo)\s+)|(?:\{|\()\s*)*'
$script:PinShellDeclarationPattern = '(?:(?:export|readonly|declare|local)(?:\s+-[A-Za-z]+)*\s+)?'

function Test-PinShellCommentStart {
    [CmdletBinding()]
    [OutputType([bool])]
    param(
        [Parameter(Mandatory)]
        [string]$Content,

        [Parameter(Mandatory)]
        [int]$Index
    )

    if ($Index -eq 0) {
        return $true
    }
    $previousCharacter = $Content[$Index - 1]
    return [char]::IsWhiteSpace($previousCharacter) -or $previousCharacter -in @(';', '&', '|', '(', ')')
}

function Get-PinCandidateFiles {
    <#
    .SYNOPSIS
        Returns tracked source files that can contain monitored assignments.
    .OUTPUTS
        System.String[]
    #>
    [CmdletBinding()]
    [OutputType([string[]])]
    param(
        [Parameter(Mandatory)]
        [string]$RepoRoot,

        [Parameter()]
        [string[]]$ExcludePatterns = @(
            '^scripts/tests/Fixtures/',
            '\.Tests\.ps1$'
        )
    )

    $gitOutput = @(git -c core.quotePath=false -C $RepoRoot ls-files -- '*.sh' '*.ps1' '*.json' '*.jsonc' 2>&1)
    $gitExitCode = $LASTEXITCODE
    if ($gitExitCode -ne 0) {
        throw "Could not enumerate tracked source files (git exit code $gitExitCode): $($gitOutput -join "`n")"
    }
    if ($gitOutput.Count -eq 0) {
        throw 'Could not enumerate tracked source files'
    }

    $candidateFiles = @(
        $gitOutput | Where-Object {
            $file = $_
            (Test-Path -LiteralPath (Join-Path $RepoRoot $file) -PathType Leaf) -and
            -not ($ExcludePatterns | Where-Object { $file -match $_ })
        }
    )
    if ($candidateFiles.Count -eq 0) {
        throw 'All tracked source-file candidates were excluded or missing from the working tree'
    }
    $candidateFiles
}

function Get-ShellCommandSegments {
    <#
    .SYNOPSIS
        Splits shell content at command boundaries outside quotes and comments.
    .OUTPUTS
        System.String[]
    #>
    [CmdletBinding()]
    [OutputType([string[]])]
    param(
        [Parameter(Mandatory)]
        [string]$Content
    )

    $segments = [System.Collections.Generic.List[string]]::new()
    $segment = [System.Text.StringBuilder]::new()
    $inSingleQuote = $false
    $inDoubleQuote = $false
    $isEscaped = $false
    $inComment = $false

    for ($index = 0; $index -lt $Content.Length; $index++) {
        $character = $Content[$index]

        if ($inComment) {
            if ($character -eq "`n") {
                $inComment = $false
                if ($segment.Length -gt 0) {
                    $segments.Add($segment.ToString())
                    [void]$segment.Clear()
                }
            }
            continue
        }

        if ($isEscaped) {
            [void]$segment.Append($character)
            $isEscaped = $false
            continue
        }

        if ($character -eq '\' -and -not $inSingleQuote) {
            [void]$segment.Append($character)
            $isEscaped = $true
            continue
        }

        if ($character -eq "'" -and -not $inDoubleQuote) {
            $inSingleQuote = -not $inSingleQuote
            [void]$segment.Append($character)
            continue
        }

        if ($character -eq '"' -and -not $inSingleQuote) {
            $inDoubleQuote = -not $inDoubleQuote
            [void]$segment.Append($character)
            continue
        }

        if ($character -eq '#' -and -not $inSingleQuote -and -not $inDoubleQuote) {
            if (Test-PinShellCommentStart -Content $Content -Index $index) {
                $inComment = $true
                continue
            }
        }

        if (
            -not $inSingleQuote -and
            -not $inDoubleQuote -and
            ($character -eq "`n" -or $character -in @(';', '&', '|', ')'))
        ) {
            if ($segment.Length -gt 0) {
                $segments.Add($segment.ToString())
                [void]$segment.Clear()
            }
            continue
        }

        [void]$segment.Append($character)
    }

    if ($segment.Length -gt 0) {
        $segments.Add($segment.ToString())
    }

    $segments.ToArray()
}

function Get-PinShellHeredocDeclarations {
    <#
    .SYNOPSIS
        Returns heredoc declarations found outside shell quotes and comments.
    .OUTPUTS
        PSCustomObject records with Delimiter and StripTabs properties.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory)]
        [AllowEmptyString()]
        [string]$Line
    )

    $inSingleQuote = $false
    $inDoubleQuote = $false
    $isEscaped = $false
    $arithmeticDepth = 0

    for ($index = 0; $index -lt $Line.Length; $index++) {
        $character = $Line[$index]

        if ($isEscaped) {
            $isEscaped = $false
            continue
        }
        if ($character -eq '\' -and -not $inSingleQuote) {
            $isEscaped = $true
            continue
        }
        if ($character -eq "'" -and -not $inDoubleQuote) {
            $inSingleQuote = -not $inSingleQuote
            continue
        }
        if ($character -eq '"' -and -not $inSingleQuote) {
            $inDoubleQuote = -not $inDoubleQuote
            continue
        }
        if ($inSingleQuote -or $inDoubleQuote) {
            continue
        }
        if (
            $arithmeticDepth -eq 0 -and
            $character -eq '$' -and
            $index + 2 -lt $Line.Length -and
            $Line[$index + 1] -eq '(' -and
            $Line[$index + 2] -eq '('
        ) {
            $arithmeticDepth = 2
            $index += 2
            continue
        }
        if ($arithmeticDepth -gt 0) {
            if ($character -eq '(') {
                $arithmeticDepth++
            }
            elseif ($character -eq ')') {
                $arithmeticDepth--
            }
            continue
        }
        if ($character -eq '#') {
            if (Test-PinShellCommentStart -Content $Line -Index $index) {
                break
            }
        }
        if (
            $character -ne '<' -or
            $index + 1 -ge $Line.Length -or
            $Line[$index + 1] -ne '<' -or
            ($index + 2 -lt $Line.Length -and $Line[$index + 2] -eq '<')
        ) {
            continue
        }

        $cursor = $index + 2
        $stripTabs = $false
        if ($cursor -lt $Line.Length -and $Line[$cursor] -eq '-') {
            $stripTabs = $true
            $cursor++
        }
        while ($cursor -lt $Line.Length -and [char]::IsWhiteSpace($Line[$cursor])) {
            $cursor++
        }
        if ($cursor -ge $Line.Length) {
            continue
        }

        $quote = if ($Line[$cursor] -in @("'", '"')) { $Line[$cursor] } else { $null }
        if ($null -ne $quote) {
            $cursor++
            $start = $cursor
            while ($cursor -lt $Line.Length -and $Line[$cursor] -ne $quote) {
                $cursor++
            }
            if ($cursor -ge $Line.Length) {
                throw 'Unterminated quoted shell heredoc delimiter'
            }
            $delimiter = $Line.Substring($start, $cursor - $start)
        }
        else {
            $delimiterBuilder = [System.Text.StringBuilder]::new()
            while (
                $cursor -lt $Line.Length -and
                -not [char]::IsWhiteSpace($Line[$cursor]) -and
                $Line[$cursor] -notin @(';', '&', '|', '<', '>')
            ) {
                if ($Line[$cursor] -eq '\' -and $cursor + 1 -lt $Line.Length) {
                    $cursor++
                }
                [void]$delimiterBuilder.Append($Line[$cursor])
                $cursor++
            }
            $delimiter = $delimiterBuilder.ToString()
        }

        if ($delimiter.Length -gt 0) {
            [pscustomobject][ordered]@{
                Delimiter = $delimiter
                StripTabs = $stripTabs
            }
        }
        $index = $cursor
    }
}

function Remove-PinShellHeredocBodies {
    <#
    .SYNOPSIS
        Removes shell heredoc bodies while preserving their command lines.
    .OUTPUTS
        System.String
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)]
        [string]$Content
    )

    $result = [System.Collections.Generic.List[string]]::new()
    $declarations = [System.Collections.Generic.Queue[object]]::new()

    foreach ($line in ($Content -split "\r?\n")) {
        if ($declarations.Count -gt 0) {
            $declaration = $declarations.Peek()
            $candidate = if ($declaration.StripTabs) { $line.TrimStart("`t") } else { $line }
            if ($candidate -eq $declaration.Delimiter) {
                [void]$declarations.Dequeue()
            }
            $result.Add('')
            continue
        }

        $result.Add($line)
        foreach ($declaration in Get-PinShellHeredocDeclarations -Line $line) {
            $declarations.Enqueue($declaration)
        }
    }

    if ($declarations.Count -gt 0) {
        throw "Unterminated shell heredoc with delimiter '$($declarations.Peek().Delimiter)'"
    }

    $result -join "`n"
}

function Get-PinJsonStringValues {
    <#
    .SYNOPSIS
        Recursively returns decoded string values from parsed JSON.
    .OUTPUTS
        System.String[]
    #>
    [CmdletBinding()]
    [OutputType([string[]])]
    param(
        [Parameter(Mandatory)]
        [AllowNull()]
        [object]$Value
    )

    if ($Value -is [string]) {
        $Value
        return
    }
    if ($Value -is [System.Collections.IDictionary]) {
        foreach ($child in $Value.Values) {
            Get-PinJsonStringValues -Value $child
        }
        return
    }
    if ($Value -is [System.Collections.IEnumerable]) {
        foreach ($child in $Value) {
            Get-PinJsonStringValues -Value $child
        }
        return
    }
}

function Get-PowerShellAssignments {
    <#
    .SYNOPSIS
        Extracts assignments for one PowerShell variable from parsed content.
    .OUTPUTS
        PSCustomObject records with IsLiteral and Value properties.
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)]
        [string]$Content,

        [Parameter(Mandatory)]
        [string]$VariableName,

        [Parameter(Mandatory)]
        [string]$File,

        [Parameter()]
        [string[]]$AllowedScopes = @('', 'script', 'global', 'local', 'private')
    )

    $tokens = $null
    $parseErrors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseInput(
        $Content,
        [ref]$tokens,
        [ref]$parseErrors
    )
    if ($parseErrors.Count -gt 0) {
        $parseError = $parseErrors[0]
        throw "Could not parse '$File' at line $($parseError.Extent.StartLineNumber): $($parseError.Message)"
    }

    # FindAll executes the predicate in a child scope, so capture the parameter locally.
    $targetVariableName = $VariableName
    $targetScopes = $AllowedScopes
    $assignments = $ast.FindAll({
            param($node)
            if (
                $node -isnot [System.Management.Automation.Language.AssignmentStatementAst] -or
                $node.Operator -ne [System.Management.Automation.Language.TokenKind]::Equals -or
                $node.Left -isnot [System.Management.Automation.Language.VariableExpressionAst]
            ) {
                return $false
            }
            $userPath = $node.Left.VariablePath.UserPath
            $separatorIndex = $userPath.LastIndexOf(':')
            $scope = if ($separatorIndex -ge 0) { $userPath.Substring(0, $separatorIndex) } else { '' }
            $name = if ($separatorIndex -ge 0) { $userPath.Substring($separatorIndex + 1) } else { $userPath }
            return $name -eq $targetVariableName -and $scope -in $targetScopes
        }, $true)

    foreach ($assignment in $assignments) {
        $expression = if ($assignment.Right -is [System.Management.Automation.Language.CommandExpressionAst]) {
            $assignment.Right.Expression
        }
        elseif (
            $assignment.Right -is [System.Management.Automation.Language.PipelineAst] -and
            $assignment.Right.PipelineElements.Count -gt 0
        ) {
            $assignment.Right.PipelineElements[0].Expression
        }
        else {
            $null
        }
        $isLiteral = $expression -is [System.Management.Automation.Language.StringConstantExpressionAst]
        [pscustomobject][ordered]@{
            IsLiteral = $isLiteral
            Value     = if ($isLiteral) {
                $expression.Value
            }
            else {
                $null
            }
        }
    }
}

function Get-PowerShellVersionAssignments {
    <#
    .SYNOPSIS
        Extracts literal version assignments from PowerShell content.
    .OUTPUTS
        PSCustomObject records with File and Version properties.
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)]
        [string]$Content,

        [Parameter(Mandatory)]
        [string]$VariableName,

        [Parameter(Mandatory)]
        [string]$SemanticVersionPattern,

        [Parameter(Mandatory)]
        [string]$File,

        [Parameter()]
        [string[]]$AllowedScopes = @('', 'script', 'global', 'local', 'private')
    )

    foreach (
        $assignment in Get-PowerShellAssignments `
            -Content $Content `
            -VariableName $VariableName `
            -File $File `
            -AllowedScopes $AllowedScopes
    ) {
        if (-not $assignment.IsLiteral) {
            throw "Found '$VariableName' in '$File' but the assignment is not a supported literal version"
        }
        $match = [regex]::Match(
            $assignment.Value,
            '^v?' + $SemanticVersionPattern + '$',
            [System.Text.RegularExpressions.RegexOptions]::None,
            $script:PinRegexTimeout
        )
        if (-not $match.Success) {
            throw "Found '$VariableName' in '$File' but '$($assignment.Value)' is not a supported literal version"
        }
        [pscustomobject][ordered]@{
            File    = $File
            Version = $match.Groups['Version'].Value
        }
    }
}

function New-PinShellAssignmentPatterns {
    <#
    .SYNOPSIS
        Builds the supported literal shell assignment pattern.
    .OUTPUTS
        PSCustomObject
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)]
        [string]$ShellVariable,

        [Parameter(Mandatory)]
        [string]$SemanticVersionPattern
    )

    $shellVariablePattern = [regex]::Escape($ShellVariable)
    $assignmentValuePattern = '(?:"\$\{' + $shellVariablePattern + ':-v?' + $SemanticVersionPattern + '\}"' +
        '|\$\{' + $shellVariablePattern + ':-v?' + $SemanticVersionPattern + '\}' +
        '|"v?' + $SemanticVersionPattern + '"|''v?' + $SemanticVersionPattern + '''|v?' +
        $SemanticVersionPattern + ')(?=\s|$)'

    $prefix = '^\s*' + $script:PinShellCommandPrefixPattern + $script:PinShellDeclarationPattern +
        $script:PinShellLeadingAssignmentPattern + $shellVariablePattern + '\s*='
    [pscustomobject][ordered]@{
        Strict = $prefix + '\s*' + $assignmentValuePattern
        Loose  = $prefix
    }
}

function Get-PinShellVersionAssignments {
    <#
    .SYNOPSIS
        Extracts literal shell version assignments from executable command segments.
    .OUTPUTS
        PSCustomObject records with File and Version properties.
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)]
        [string]$Content,

        [Parameter(Mandatory)]
        [string]$ShellVariable,

        [Parameter(Mandatory)]
        [pscustomobject]$ShellPatterns,

        [Parameter(Mandatory)]
        [string]$File
    )

    $cleanContent = Remove-PinShellHeredocBodies -Content $Content
    $segments = @(Get-ShellCommandSegments -Content $cleanContent)
    $assignments = foreach ($segment in $segments) {
        try {
            $match = [regex]::Match(
                $segment,
                $ShellPatterns.Strict,
                [System.Text.RegularExpressions.RegexOptions]::None,
                $script:PinRegexTimeout
            )
        }
        catch [System.Text.RegularExpressions.RegexMatchTimeoutException] {
            throw "Timed out parsing '$ShellVariable' assignment in '$File'"
        }
        if ($match.Success) {
            [pscustomobject][ordered]@{
                File    = $File
                Version = $match.Groups['Version'].Value
            }
            continue
        }
        try {
            $looseMatch = [regex]::Match(
                $segment,
                $ShellPatterns.Loose,
                [System.Text.RegularExpressions.RegexOptions]::None,
                $script:PinRegexTimeout
            )
        }
        catch [System.Text.RegularExpressions.RegexMatchTimeoutException] {
            throw "Timed out parsing '$ShellVariable' assignment in '$File'"
        }
        if ($looseMatch.Success) {
            throw "Found '$ShellVariable' in '$File' but the assignment is not a supported literal version"
        }
    }

    @($assignments)
}

function Resolve-PinCandidatePath {
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory)]
        [string]$File,

        [Parameter(Mandatory)]
        [string]$RepoRoot,

        [Parameter(Mandatory)]
        [string]$RootPrefix
    )

    $path = Join-Path $RepoRoot $File
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Tracked pin candidate is missing from the working tree: '$File'"
    }
    $item = Get-Item -LiteralPath $path -Force
    $resolvedPath = if ($item.LinkType) {
        $linkTarget = $item.ResolveLinkTarget($true)
        if ($null -eq $linkTarget) {
            throw "Could not resolve link target for pin candidate '$File'"
        }
        $linkTarget.FullName
    }
    else {
        $item.FullName
    }
    $canonicalPath = [System.IO.Path]::GetFullPath($resolvedPath)
    if (-not $canonicalPath.StartsWith($RootPrefix, [System.StringComparison]::Ordinal)) {
        throw "Pin candidate resolves outside the repository: '$File'"
    }
    $canonicalPath
}

function Get-PinJsonVersionAssignments {
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)]
        [string]$Content,

        [Parameter(Mandatory)]
        [string]$File,

        [Parameter(Mandatory)]
        [string]$ShellVariable,

        [Parameter(Mandatory)]
        [pscustomobject]$ShellPatterns
    )

    try {
        # The default depth is too shallow for nested devcontainer command structures.
        $json = $Content | ConvertFrom-Json -AsHashtable -Depth 100
    }
    catch {
        throw "Could not parse '$File' as JSON: $($_.Exception.Message)"
    }
    foreach ($value in Get-PinJsonStringValues -Value $json) {
        Get-PinShellVersionAssignments `
            -Content $value `
            -ShellVariable $ShellVariable `
            -ShellPatterns $ShellPatterns `
            -File $File
    }
}

function Get-PinnedToolVersionAssignments {
    <#
    .SYNOPSIS
        Extracts unique file and version pairs for one monitored tool.
    .PARAMETER ShellVariable
        Shell variable name used in shell and JSON command assignments.
    .PARAMETER Files
        Repository-relative candidate paths.
    .PARAMETER RepoRoot
        Repository root used to resolve and contain candidate paths.
    .PARAMETER PowerShellVariable
        Optional PowerShell variable name used in assignment statements.
    .OUTPUTS
        PSCustomObject records with File and Version properties.
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)]
        [string]$ShellVariable,

        [Parameter(Mandatory)]
        [string[]]$Files,

        [Parameter(Mandatory)]
        [string]$RepoRoot,

        [Parameter()]
        [string]$PowerShellVariable
    )

    $semanticVersion = '(?<Version>[0-9]+(?:\.[0-9]+)+(?:[-+][0-9A-Za-z.-]+)?)'
    $shellVariablePattern = [regex]::Escape($ShellVariable)
    $shellPatterns = New-PinShellAssignmentPatterns `
        -ShellVariable $ShellVariable `
        -SemanticVersionPattern $semanticVersion

    $canonicalRoot = [System.IO.Path]::GetFullPath($RepoRoot).TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
    $rootPrefix = $canonicalRoot + [System.IO.Path]::DirectorySeparatorChar

    $pins = foreach ($file in $Files) {
        $canonicalPath = Resolve-PinCandidatePath -File $file -RepoRoot $RepoRoot -RootPrefix $rootPrefix
        $content = Get-Content -LiteralPath $canonicalPath -Raw
        if ([string]::IsNullOrEmpty($content)) {
            continue
        }

        $extension = [System.IO.Path]::GetExtension($canonicalPath)
        if ($extension -eq '.ps1') {
            if (
                $PowerShellVariable -and
                $content -match ('\$(?:(?:script|global|local|private):)?' +
                    [regex]::Escape($PowerShellVariable) + '\b')
            ) {
                Get-PowerShellVersionAssignments `
                    -Content $content `
                    -VariableName $PowerShellVariable `
                    -SemanticVersionPattern $semanticVersion `
                    -File $file
            }
            if ($content -match ('\$env:' + $shellVariablePattern + '\b')) {
                Get-PowerShellVersionAssignments `
                    -Content $content `
                    -VariableName $ShellVariable `
                    -SemanticVersionPattern $semanticVersion `
                    -File $file `
                    -AllowedScopes @('env')
            }
            continue
        }
        if ($content -notmatch ('\b' + $shellVariablePattern + '\b')) {
            continue
        }

        if ($extension -eq '.sh') {
            Get-PinShellVersionAssignments `
                -Content $content `
                -ShellVariable $ShellVariable `
                -ShellPatterns $shellPatterns `
                -File $file
            continue
        }

        if ($extension -notin @('.json', '.jsonc')) {
            continue
        }

        Get-PinJsonVersionAssignments `
            -Content $content `
            -File $file `
            -ShellVariable $ShellVariable `
            -ShellPatterns $shellPatterns
    }

    @($pins | Sort-Object File, Version -Unique)
}

function Get-PinnedToolFreshness {
    <#
    .SYNOPSIS
        Classifies discovered assignments against the latest release.
    .OUTPUTS
        PSCustomObject
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [object[]]$Assignments,

        [Parameter(Mandatory)]
        [string]$LatestVersion
    )

    $pinnedVersions = @($Assignments.Version | Sort-Object -Unique)
    [pscustomobject][ordered]@{
        PinnedVersions = $pinnedVersions
        IsStale        = @($Assignments | Where-Object { $_.Version -ne $LatestVersion }).Count -gt 0
        IsInconsistent = $pinnedVersions.Count -gt 1
    }
}

Export-ModuleMember -Function @(
    'Get-PinCandidateFiles',
    'Get-PinnedToolVersionAssignments',
    'Get-PowerShellAssignments',
    'Get-PinnedToolFreshness'
)
