/* Playwright CLI run-code regression. Use an isolated service on 127.0.0.1:8868.
 * Run in desktop Chrome, desktop WebKit, and touch WebKit. This creates one test brief.
 * A mouse click must reach the option before focus-out dismisses the popup.
 */
async page => {
  const base = new URL(page.url()).origin;
  if (base !== "http://127.0.0.1:8868") throw Error("Use the isolated test service on port 8868");
  await page.goto(base + "/#/briefs/new");
  await page.reload();
  await page.locator("#locationGroups select").first().locator("option[value=RU]").waitFor({state: "attached"});
  const group = page.getByRole("group", {name: "Локация 1", exact: true});
  await group.getByRole("combobox", {name: "Страна", exact: true}).selectOption("RU");
  const input = group.getByRole("combobox", {name: "Города", exact: true});
  const touch = await page.evaluate(() => navigator.maxTouchPoints > 0 || matchMedia("(pointer: coarse)").matches);
  const choose = async (text, name) => {
    await input.fill("");
    await input.pressSequentially(text, {delay: 75});
    const option = group.getByRole("option", {name, exact: true});
    await option.waitFor({state: "visible"});
    if (touch) await option.tap();
    else await option.click();
  };
  await choose("моск", "Москва · Russia · Moscow");
  await group.getByRole("button", {name: "Убрать город Москва", exact: true}).waitFor({timeout: 2000});
  if (await input.inputValue() !== "") throw Error("The chosen city did not clear its search text");
  if (!(await page.locator("#locationCount").innerText()).startsWith("1 из 5"))
    throw Error("The chosen city was not counted");

  // Whole-country choice uses the same pointer path as a city.
  const wholeCountry = group.getByRole("option", {name: "Вся страна", exact: true});
  if (touch) await wholeCountry.tap();
  else await wholeCountry.click();
  await group.getByRole("button", {name: "Убрать выбор всей страны", exact: true}).waitFor();
  await choose("моск", "Москва · Russia · Moscow");
  await group.getByRole("button", {name: "Убрать город Москва", exact: true}).waitFor({timeout: 2000});
  await choose("Казань", "Казань · Russia · Tatarstan Republic");
  await group.getByRole("button", {name: "Убрать город Казань", exact: true}).waitFor({timeout: 2000});

  // Retaining pointer focus must not break keyboard selection or outside dismissal.
  await input.fill("");
  await input.press("ArrowDown");
  await wholeCountry.press("Escape");
  if (await input.getAttribute("aria-expanded") !== "false") throw Error("Escape did not close the picker");
  await input.click();
  await page.getByRole("heading", {name: "Название брифа", exact: true}).click();
  if (await input.getAttribute("aria-expanded") !== "false") throw Error("Outside click did not close the picker");

  await page.locator("#name").fill("Regression · city picker · " + Date.now());
  await page.locator("#queries").fill("QA");
  await page.locator("#queries").press("Enter");
  const saved = page.waitForResponse(response => response.url() === base + "/api/v1/briefs"
    && response.request().method() === "POST");
  await page.getByRole("button", {name: "Только сохранить", exact: true}).click();
  const response = await saved;
  if (response.status() !== 201) throw Error("Saving the chosen cities failed: " + await response.text());
  const brief = await response.json();
  const places = brief.filters.locations;
  if (places.length !== 2 || places[0].geoname_id !== 524901 || places[1].geoname_id !== 551487
      || places.some(place => place.scope !== "city")) throw Error("Chosen cities were not saved correctly");
  await page.waitForURL(/folder/);
  await page.getByRole("button", {name: "Поиск и условия", exact: true}).click();
  await group.getByRole("button", {name: "Убрать город Москва", exact: true}).waitFor();
  await group.getByRole("button", {name: "Убрать город Казань", exact: true}).waitFor();
  await page.evaluate(result => {window.cityPickerRegression = result;}, {passed: true, touch, brief_id: brief.id});
}
