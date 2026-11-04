/* Playwright CLI run-code regression against the isolated service on port 8868.
 * Verify pointer/touch and keyboard retries after failed city suggestions.
 */
async page => {
  const base = new URL(page.url()).origin;
  if (base !== "http://127.0.0.1:8868") throw Error("Use the isolated test service on port 8868");
  const touch = await page.evaluate(() => navigator.maxTouchPoints > 0 || matchMedia("(pointer: coarse)").matches);
  for (const interaction of ["pointer", "keyboard"]) {
    await page.goto(base + "/#/briefs/new");
    await page.reload();
    const group = page.getByRole("group", {name: "Локация 1", exact: true});
    const country = group.getByRole("combobox", {name: "Страна", exact: true});
    await country.locator("option[value=RU]").waitFor({state: "attached"});
    await country.selectOption("RU");
    const input = group.getByRole("combobox", {name: "Города", exact: true});
    const retry = group.getByRole("button", {name: "Повторить поиск города", exact: true});
    let fail = true;
    const routeHandler = route => fail
      ? route.fulfill({status: 503, contentType: "application/json", body: JSON.stringify({detail: "Temporary city failure"})})
      : route.continue();
    await page.route("**/api/v1/geography/cities?**", routeHandler);
    try {
      await input.fill("моск");
      await retry.waitFor({state: "visible"});
      const activate = async () => {
        if (interaction === "keyboard") {await retry.focus(); await retry.press("Enter");}
        else if (touch) await retry.tap();
        else await retry.click();
      };
      // A repeated error must leave the retry action visible and usable.
      const failedResponse = page.waitForResponse(response => response.url().includes("/geography/cities?")
        && response.status() === 503, {timeout: 3000});
      await activate();
      await failedResponse;
      await retry.waitFor({state: "visible"});
      if (await input.getAttribute("aria-expanded") !== "true")
        throw Error(interaction + ": retry failure closed the picker");
      fail = false;
      const recoveredResponse = page.waitForResponse(response => response.url().includes("/geography/cities?")
        && response.status() === 200, {timeout: 3000});
      await activate();
      await recoveredResponse;
      const city = group.getByRole("option", {name: "Москва · Russia · Moscow", exact: true});
      await city.waitFor({state: "visible"});
      if (interaction === "keyboard") {await input.press("ArrowDown"); await page.keyboard.press("ArrowDown"); await page.keyboard.press("Enter");}
      else if (touch) await city.tap();
      else await city.click();
      await group.getByRole("button", {name: "Убрать город Москва", exact: true}).waitFor();
      if (await input.inputValue() !== "" || await page.locator("#locationCount").innerText() !== "1 из 5 мест поиска")
        throw Error(interaction + ": recovered city was not selected correctly");
      await input.press("Escape");
      await input.click();
      await page.getByRole("heading", {name: "Название брифа", exact: true}).click();
      if (await input.getAttribute("aria-expanded") !== "false")
        throw Error(interaction + ": outside click did not close the recovered picker");
    } finally {
      await page.unroute("**/api/v1/geography/cities?**", routeHandler);
    }
  }
}
