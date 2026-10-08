// The time zone picker, filled from the browser's own list and its current zone.

export function timeZones(select, current) {
  const here = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  let zones = [];
  try { zones = Intl.supportedValuesOf("timeZone"); } catch {}
  const pick = current || here;
  if (!zones.includes(pick)) zones.unshift(pick);
  select.replaceChildren(...zones.map((z) => new Option(z.replaceAll("_", " "), z, false, z === pick)));
  return pick;
}
