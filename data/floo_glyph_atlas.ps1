param(
  [Parameter(Mandatory = $true)][string]$CharsFile,
  [Parameter(Mandatory = $true)][string]$OutPng,
  [Parameter(Mandatory = $true)][string]$OutAdvances,
  [int]$Size = 32,
  [int]$CellW = 48,
  [int]$CellH = 56,
  [int]$Columns = 100
)
# Render reference glyphs the way Floo's reader images are drawn: GDI+
# ClearType text in Microsoft YaHei, black on white. One glyph per cell,
# drawn at (8, 8). Also writes each glyph's advance width in pixels.
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$text = [System.IO.File]::ReadAllText($CharsFile, [System.Text.Encoding]::UTF8)
$chars = New-Object System.Collections.Generic.List[string]
$e = [System.Globalization.StringInfo]::GetTextElementEnumerator($text)
while ($e.MoveNext()) { $chars.Add([string]$e.GetTextElement()) }
$font = New-Object System.Drawing.Font('Microsoft YaHei', $Size, [System.Drawing.FontStyle]::Regular, [System.Drawing.GraphicsUnit]::Pixel)
$rows = [math]::Ceiling($chars.Count / $Columns)
$bmp = New-Object System.Drawing.Bitmap(($Columns * $CellW), ($rows * $CellH))
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.Clear([System.Drawing.Color]::White)
$g.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::ClearTypeGridFit
$fmt = [System.Drawing.StringFormat]::GenericTypographic
$fmt.FormatFlags = $fmt.FormatFlags -bor [System.Drawing.StringFormatFlags]::MeasureTrailingSpaces
$advances = New-Object System.Collections.Generic.List[string]
for ($i = 0; $i -lt $chars.Count; $i++) {
  $x = ($i % $Columns) * $CellW + 8
  $y = [math]::Floor($i / $Columns) * $CellH + 8
  $g.DrawString($chars[$i], $font, [System.Drawing.Brushes]::Black, [single]$x, [single]$y, $fmt)
  $w = $g.MeasureString($chars[$i], $font, [System.Drawing.PointF]::new(0, 0), $fmt).Width
  $advances.Add([string]::Format([System.Globalization.CultureInfo]::InvariantCulture, '{0:0.###}', $w))
}
$bmp.Save($OutPng, [System.Drawing.Imaging.ImageFormat]::Png)
[System.IO.File]::WriteAllText($OutAdvances, ($advances -join "`n"), [System.Text.Encoding]::UTF8)
$g.Dispose(); $bmp.Dispose()
