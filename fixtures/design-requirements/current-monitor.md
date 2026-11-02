# Qwiic current monitor

Build a small INA219 high-side current monitor for a nominal 12 V load at up to
1 A. Provide separate two-terminal source and load connections and an onboard
shunt. Bring the I2C interface and 3.3 V logic supply to a Qwiic connector with the
usual ground, supply, SDA, SCL order. Include pull-ups and local bypassing; choose
a fixed address and label it. The load supply must not feed the logic supply.
Use stocked parts and leave shunt calibration and thermal verification as bench
work. Keep the board compact enough for a cable-mounted breakout.
