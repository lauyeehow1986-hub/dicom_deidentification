# Entry point. Run with: shiny::runApp(".")
#
# During development we source the R/ modules directly (no install step needed so
# the whole app is copy-movable onto the air-gapped box).

for (f in list.files("R", pattern = "\\.R$", full.names = TRUE)) source(f)

shiny::shinyApp(ui = app_ui(), server = app_server)
