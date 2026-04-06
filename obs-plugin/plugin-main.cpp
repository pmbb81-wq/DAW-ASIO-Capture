#include <obs-module.h>
#include "daw-source.hpp"

OBS_DECLARE_MODULE()
OBS_MODULE_USE_DEFAULT_LOCALE("obs-daw-capture", "en-US")

bool obs_module_load(void)
{
    obs_register_source(&daw_capture_source_info);
    return true;
}

void obs_module_unload(void)
{
}

MODULE_EXPORT const char *obs_module_description(void)
{
    return "DAW Audio Capture — taps ASIO output from your DAW and feeds it "
           "to OBS with a ~40ms tape-style delay. Set Monitor Off in OBS so "
           "stream viewers hear it but you don't.";
}
