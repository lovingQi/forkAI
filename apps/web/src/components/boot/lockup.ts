/** 页眉标志。四个汉字内嵌 Noto Sans SC 子集，车载环境没有中文字体时也不会变成方框。 */
const NAME_FONT = "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"

export function lockupSvg(): string {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="280" viewBox="0 0 1024 280">
  <defs>
    <style type="text/css"><![CDATA[
      @font-face {
        font-family: "JiuwuSC";
        src: url("data:font/woff;base64,${NAME_FONT}") format("woff");
        font-weight: 700;
        font-style: normal;
      }
    ]]></style>
  </defs>
  <circle cx="140" cy="140" r="108" fill="#1B2A6B"/>
  <path d="M168 62 C168 62, 58 74, 54 138 C50 206, 132 222, 164 176 C184 148, 150 126, 124 140" fill="none" stroke="#ffffff" stroke-width="22" stroke-linecap="round" stroke-linejoin="round"/>
  <path d="M158 70 L176 222" fill="none" stroke="#ffffff" stroke-width="22" stroke-linecap="round"/>
  <circle cx="214" cy="48" r="18" fill="#C4343A"/>
  <text x="276" y="162" fill="#1B2A6B" font-size="92" font-weight="700" font-family="JiuwuSC, PingFang SC, Microsoft YaHei, Noto Sans SC, sans-serif">玖物智能</text>
  <rect x="672" y="78" width="4" height="132" fill="#1B2A6B"/>
  <text x="704" y="136" font-size="64" font-weight="700" font-family="PingFang SC, Microsoft YaHei, Noto Sans SC, sans-serif"><tspan fill="#C4343A">R</tspan><tspan fill="#1B2A6B">eshape</tspan></text>
  <text x="704" y="198" fill="#1B2A6B" font-size="48" font-weight="600" font-family="PingFang SC, Microsoft YaHei, Noto Sans SC, sans-serif">Manufacturing</text>
</svg>`
}
