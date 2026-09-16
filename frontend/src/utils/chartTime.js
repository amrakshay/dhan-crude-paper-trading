/**
 * lightweight-charts renders every timestamp in UTC and offers no timezone
 * option. MCX quotes in IST, so a 09:00 bar would be drawn as 03:30 unless the
 * series is shifted.
 *
 * IST is a fixed +05:30 with no daylight saving, so adding the offset before
 * handing a time to the chart -- and subtracting it on the way back -- is exact
 * rather than an approximation. Every timestamp crossing into the chart library
 * goes through these two functions; nothing else should do the arithmetic.
 */
export const IST_OFFSET_SECONDS = 5.5 * 3600;

/** Epoch seconds -> the value the chart should draw as IST wall-clock time. */
export function toChartTime(epochSeconds) {
  return epochSeconds + IST_OFFSET_SECONDS;
}

/** The inverse, for reading a time back off the chart. */
export function fromChartTime(chartTime) {
  return chartTime - IST_OFFSET_SECONDS;
}
