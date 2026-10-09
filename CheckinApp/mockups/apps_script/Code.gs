/**
 * Serves the check-in app preview as a web page.
 * Deploy > New deployment > Web app, then share the web app URL.
 */
function doGet() {
  return HtmlService.createHtmlOutputFromFile('Index')
    .setTitle('Trial Check-in Preview')
    .addMetaTag('viewport', 'width=device-width, initial-scale=1')
    .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.DEFAULT);
}
