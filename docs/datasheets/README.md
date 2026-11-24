# Datasheets

Pololu publishes no datasheet PDF for these regulators. Saved 2026-11-22:

| File | What |
|---|---|
| `pololu-d36v28fx-dimensions.pdf` | Dimension drawing, shared by the D36V28Fx family |
| `pololu-3782-product-page.html` | D36V28F5 (5 V) product page, text only: specs, pinout, graphs' captions |
| `pololu-3783-product-page.html` | D36V28F6 (6 V) product page, same |
| `adafruit-1385-mp2307-datasheet.pdf` | MP2307 3 A synchronous buck regulator datasheet (12 pages), the chip on the Adafruit UBEC (MP2307_r1.9.pdf) |
| `adafruit-1385-product-page.md` | Adafruit #1385 product page: description and technical details text, saved 2026-11-24 |

Adafruit UBEC 5 V 3 A (#1385), from Adafruit's page and the MP2307 datasheet:

| | Adafruit #1385 (board, from product page) |
|---|---|
| Input range | "connect 6V up to 16V (recommended)"; the label on the board says "provide 7V-12V" |
| Output voltage | 5 V, "5V (+-5%) regulated output" |
| Continuous current | 3 A ("3A output current") |
| Peak current | 5 A ("5A peak") |
| Input capacitor | "The input capacitor might be rated for 16V max." |

Chip-level figures from the MP2307 datasheet (not board ratings): input 4.75 V to 23 V; output adjustable 0.925 V to 20 V; 3 A continuous, 4 A peak output current.

Sources: https://www.adafruit.com/product/1385 (product page, description and technical details) and
https://cdn-shop.adafruit.com/datasheets/MP2307_r1.9.pdf (linked from the product page, as
http://www.adafruit.com/datasheets/MP2307_r1.9.pdf).

Key specs, from Pololu's spec pages:

| | D36V28F5 (#3782) | D36V28F6 (#3783) |
|---|---|---|
| Output | 5 V fixed | 6 V fixed |
| Continuous current (typical max, at 36 V in) | 3.2 A | 2.7 A |
| Input | 5.3 to 50 V | 6.3 to 50 V |
| Reverse-voltage protection | to -40 V | to -40 V |
| Quiescent current, enabled, no load | 3 mA max | 3 mA max |
| Size, weight | 0.7 x 0.8 x 0.345 in, 3.3 g | same |

Sources: https://www.pololu.com/product/3782/specs and
https://www.pololu.com/product/3783/specs
