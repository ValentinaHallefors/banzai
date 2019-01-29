import abc


class CalibrationProduct(abc.ABC):

    def __init__(self, product):
        self.product = product

    @abc.abstractmethod
    def obstype(self):
        pass

    @abc.abstractmethod
    def dateobs(self):
        pass

    @abc.abstractmethod
    def datecreated(self):
        pass

    @abc.abstractmethod
    def instrument_id(self):
        pass

    @abc.abstractmethod
    def is_master(self):
        pass

    @abc.abstractmethod
    def is_bad(self):
        pass

    @abc.abstractmethod
    def attributes(self):
        pass


class ImageCalibrationProduct(CalibrationProduct):

    def __init__(self, image):
        super(ImageCalibrationProduct, self).__init__(image)

    def obstype(self):
        return self.product.obstype.upper()

    def dateobs(self):
        return self.product.dateobs

    def datecreated(self):
        return self.product.datecreated

    def instrument_id(self):
        return self.product.instrument.id

    def is_master(self):
        return self.product.is_master

    def is_bad(self):
        return self.product.is_bad

    def attributes(self):
        return self.product.attributes
