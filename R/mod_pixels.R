#' Pixel / burned-in-PHI review and redaction (Phase 3).
#'
#' Loads a DICOM image, shows the frame with OCR-proposed boxes (when a Tesseract
#' binary is bundled), lets the reviewer add manual redaction rectangles, previews
#' them, and writes a valid, viewable de-identified copy with the regions blacked
#' out and any audio/waveform data stripped.

mod_pixels_ui <- function(id) {
  ns <- shiny::NS(id)
  bslib::layout_sidebar(
    sidebar = bslib::sidebar(
      width = 380,
      shiny::textInput(ns("path"), "DICOM file", placeholder = "C:/path/to/image.dcm"),
      shiny::textInput(ns("out"), "Redacted output file",
                       placeholder = "C:/path/to/image_redacted.dcm"),
      shiny::p(class = "small text-muted",
               "Redacts burned-in pixels + strips audio only. Run the ",
               shiny::strong("Interactive"),
               " metadata de-id first, then load its output here."),
      shiny::actionButton(ns("load"), "Load image", class = "btn-primary",
                          icon = shiny::icon("image")),
      shiny::uiOutput(ns("geometry")),
      shiny::hr(),
      shiny::strong("Add a redaction box"),
      shiny::div(
        class = "d-flex gap-2",
        shiny::numericInput(ns("bx"), "x", 0, min = 0),
        shiny::numericInput(ns("by"), "y", 0, min = 0)
      ),
      shiny::div(
        class = "d-flex gap-2",
        shiny::numericInput(ns("bw"), "w", 50, min = 1),
        shiny::numericInput(ns("bh"), "h", 20, min = 1)
      ),
      shiny::div(
        class = "d-flex gap-2 align-items-end",
        shiny::actionButton(ns("add"), "Add box", icon = shiny::icon("plus")),
        shiny::actionButton(ns("clear"), "Clear", icon = shiny::icon("trash"))
      ),
      shiny::hr(),
      shiny::actionButton(ns("apply"), "Apply redaction & write",
                          class = "btn-danger", icon = shiny::icon("user-shield")),
      shiny::uiOutput(ns("result"))
    ),
    bslib::card(
      bslib::card_header("Frame preview \u2014 red outlines will be blacked out"),
      bslib::card_body(
        shiny::uiOutput(ns("frame_ctrl")),
        shiny::uiOutput(ns("image")),
        shiny::tableOutput(ns("box_table"))
      )
    )
  )
}

mod_pixels_server <- function(id, app_state) {
  shiny::moduleServer(id, function(input, output, session) {
    info  <- shiny::reactiveVal(NULL)
    boxes <- shiny::reactiveVal(list())
    frame <- shiny::reactiveVal(0L)
    done  <- shiny::reactiveVal(NULL)

    engine_ok <- function() {
      if (!isTRUE(app_state$engine$available)) {
        shiny::showNotification("Python engine not configured.", type = "error")
        return(FALSE)
      }
      TRUE
    }

    shiny::observeEvent(input$load, {
      shiny::req(input$path); if (!engine_ok()) return()
      out <- tryCatch(engine_pixel_info(input$path, app_state$profile_id %||% "default"),
                      error = function(e) {
        shiny::showNotification(paste("Error:", conditionMessage(e)), type = "error")
        NULL
      })
      shiny::req(out)
      info(out)
      frame(0L)
      done(NULL)
      # seed the box list with any OCR-proposed boxes
      boxes(if (length(out$boxes)) out$boxes else list())
    })

    output$geometry <- shiny::renderUI({
      i <- info(); if (is.null(i)) return(shiny::p(class = "text-muted small",
                                                   "Load an image to begin."))
      shiny::tagList(
        shiny::p(class = "small mb-1",
                 sprintf("%d\u00d7%d, %d frame(s), %s",
                         i$cols, i$rows, i$frames, i$photometric)),
        if (!is.null(i$ocr_note))
          shiny::p(class = "small text-warning", sprintf("OCR: %s", i$ocr_note))
        else if (length(i$boxes))
          shiny::p(class = "small text-success",
                   sprintf("%d text region(s) auto-detected.", length(i$boxes)))
      )
    })

    output$frame_ctrl <- shiny::renderUI({
      i <- info(); shiny::req(i)
      if (i$frames <= 1) return(NULL)
      shiny::sliderInput(session$ns("frame"), "Frame",
                         min = 0, max = i$frames - 1, value = frame(), step = 1)
    })
    shiny::observeEvent(input$frame, frame(as.integer(input$frame)), ignoreNULL = TRUE)

    current_boxes <- shiny::reactive(boxes())

    output$image <- shiny::renderUI({
      i <- info(); shiny::req(i, input$path)
      b64 <- tryCatch(
        engine_pixel_frame_png(input$path, frame = frame(), boxes = current_boxes()),
        error = function(e) NULL)
      shiny::req(b64)
      shiny::img(src = paste0("data:image/png;base64,", b64),
                 style = "max-width:100%;border:1px solid #ccc;")
    })

    output$box_table <- shiny::renderTable({
      bs <- current_boxes()
      if (!length(bs)) return(NULL)
      do.call(rbind, lapply(bs, function(b) data.frame(
        frame = b$frame %||% "all", x = b$x, y = b$y, w = b$w, h = b$h,
        source = b$source %||% "manual", stringsAsFactors = FALSE)))
    }, striped = TRUE, spacing = "xs")

    shiny::observeEvent(input$add, {
      shiny::req(info())
      nb <- list(x = as.integer(input$bx), y = as.integer(input$by),
                 w = as.integer(input$bw), h = as.integer(input$bh),
                 frame = frame(), source = "manual")
      boxes(c(boxes(), list(nb)))
    })
    shiny::observeEvent(input$clear, boxes(list()))

    shiny::observeEvent(input$apply, {
      shiny::req(input$path, input$out); if (!engine_ok()) return()
      if (!length(boxes())) {
        shiny::showNotification("Add at least one redaction box first.", type = "warning")
        return()
      }
      rec <- tryCatch(
        engine_pixel_redact(input$path, input$out, boxes = boxes()),
        error = function(e) {
          shiny::showNotification(paste("Error:", conditionMessage(e)), type = "error")
          NULL
        })
      shiny::req(rec)
      done(rec)
      shiny::showNotification("Redacted DICOM written.", type = "message")
    })

    output$result <- shiny::renderUI({
      r <- done(); if (is.null(r)) return(NULL)
      shiny::tagList(
        shiny::hr(),
        shiny::p(class = "small text-success",
                 sprintf("Wrote %d box(es) across %d frame(s)%s.",
                         r$boxes %||% 0, r$frames %||% 0,
                         if (isTRUE(r$decompressed)) "; decompressed to uncompressed DICOM" else "")),
        if (!is.null(r$waveforms_removed) && r$waveforms_removed > 0)
          shiny::p(class = "small", sprintf("Removed %d waveform/audio sequence(s).",
                                            r$waveforms_removed))
      )
    })
  })
}
