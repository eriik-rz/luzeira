from luzeira.sistema import paquete


def test_packaged_when_windows_asks_for_a_bigger_buffer():
    assert paquete.is_packaged(lambda: paquete.ERROR_INSUFFICIENT_BUFFER) is True


def test_not_packaged_without_package_identity():
    assert paquete.is_packaged(lambda: paquete.APPMODEL_ERROR_NO_PACKAGE) is False


def test_any_doubt_means_the_normal_version():
    def missing():
        raise AttributeError("GetCurrentPackageFullName")

    assert paquete.is_packaged(missing) is False
    assert paquete.is_packaged(lambda: 87) is False  # ERROR_INVALID_PARAMETER


def test_this_test_run_is_not_packaged():
    # pytest corre fuera de cualquier paquete: la función real tiene que decir que no.
    assert paquete.is_packaged() is False
