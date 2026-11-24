# Adafruit #1385: UBEC DC/DC Step-Down (Buck) Converter - 5V @ 3A output

- URL: https://www.adafruit.com/product/1385
- Saved: 2026-11-24

Text of the Description and Technical Details tabs, copied from the page. Images, navigation, and related-product lists are omitted.

## Description

Your power supply problems just got SOLVED! This little circuit board may look tiny but inside is a high efficiency DC/DC step-down converter which can output up to 3 Amp at 5V without the need of any heat-sink or forced cooling. (It does get a bit toasty at 3A though) UBEC stands for "universal battery eliminator circuit" and this UBEC is designed to replace a 5V supply in RC planes and 'copters but its also great for any kind of microcontroller or electronics project that runs off of 5V. We tried a half dozen different 'BECs and found this one to be the best in terms of range and stability. You can check the technical tab for the analysis of input/output range and current draw.

To use, connect 6V up to 16V (recommended) to the IN-side wires and 5V will be generated on the OUT-side wires. You'll get 5V (+-5%) regulated output from higher voltage input. 3A output current, 5A peak - if you're trying to draw 3A you'll need to give it a little more than 6V though, 7V works well. The UBEC has built in filter capacitors on the input and output, but its a switching supply so its a little noisy compared to a linear regulator.

There is a label on the clear heat shrink around the circuit board for the UBEC. The IN side (provide 7V-12V) is the open wires and the out (supplies 5V) is the black 3 pin female connector.

We pulled the heatshrink off of one and found an MP2307 you can check the datasheet for conversion efficiency and other specifications.

## Technical Details

We pulled the heatshrink off of one and found an MP2307 you can check the datasheet for conversion efficiency and other specifications. The input capacitor might be rated for 16V max.

Here are some numbers we found when attaching various loads to the UBEC:

| Iout | Vin | Iin | Vout |
|---|---|---|---|
| 0.5A | 6V | 0.47A | 4.9V |
| 0.5A | 7V | 0.39A | 5.2V |
| 0.5A | 9V | 0.35A | 5.2V |
| 0.5A | 12V | 0.28A | 5.25V |
| 1A | 6V | 0.9A | 4.8V |
| 1A | 7V | 0.79A | 5.2V |
| 1A | 9V | 0.67A | 5.1V |
| 1A | 12V | 0.52A | 5.2V |
| 2A | 6V | 1.85A | 4.9V |
| 2A | 7V | 1.67A | 5.1V |
| 2A | 9V | 1.28A | 5.0V |
| 2A | 12V | 0.97A | 4.9V |
| 3A | 7V | 2.6A | 5.0V |
| 3A | 9V | 1.88A | 4.7V |
| 3A | 12V | 1.43A | 4.8V |
