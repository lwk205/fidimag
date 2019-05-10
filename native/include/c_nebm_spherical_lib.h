#include "math.h"

void normalise_spherical(double * a, int n);

void normalise_images_spherical_C(double * y, int n_images,
                                  int n_dofs_image);

double compute_distance_spherical(double * A, double * B, int n,
                                  int * material, int n_dofs_image_material
                                  );
