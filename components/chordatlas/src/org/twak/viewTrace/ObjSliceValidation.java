package org.twak.viewTrace;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

import javax.vecmath.Point3d;
import javax.vecmath.Vector3d;

import org.twak.utils.geom.Line3d;
import org.twak.utils.geom.LinearForm3D;
import org.twak.utils.geom.ObjRead;

/** Lightweight regression check for zero-area faces in generated meshes. */
public final class ObjSliceValidation {

	private ObjSliceValidation() {}

	public static void main( String[] args ) throws Exception {
		Path obj = Files.createTempFile( "obj-slice-degenerate-", ".obj" );
		try {
			Files.write( obj, (
					"v 0 0 0\n" +
					"v 0 0 0\n" +
					"v 0 0 0\n" +
					"v -1 0 0\n" +
					"v 1 1 0\n" +
					"v 1 -1 0\n" +
					"f 1 2 3\n" +
					"f 4 5 6\n" ).getBytes( StandardCharsets.UTF_8 ) );

			ObjRead mesh = new ObjRead( obj.toFile() );
			LinearForm3D plane = new LinearForm3D( new Vector3d( 1, 0, 0 ), new Point3d() );
			List<Line3d> lines = ObjSlice.sliceTri(
					mesh, plane, 0.5, new Vector3d( 0, 0, 1 ), Math.PI );
			if ( lines.isEmpty() )
				throw new AssertionError( "valid triangle was not sliced after ignoring the degenerate face" );
		} finally {
			Files.deleteIfExists( obj );
		}
		System.out.println( "ObjSlice degenerate-face validation passed" );
	}
}
