set(_motion_srcs "face_input.cpp")
if(CONFIG_STACKCHAN_SERVO_ENABLED)
    list(PREPEND _motion_srcs "motion.cpp")
endif()

idf_component_register(
    SRCS ${_motion_srcs}
    INCLUDE_DIRS "include"
)
target_compile_features(${COMPONENT_LIB} PUBLIC cxx_std_20)
